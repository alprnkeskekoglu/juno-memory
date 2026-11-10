"""Settle stale stub cards (replace, dismiss) and duplicate cards (merge), one undoable write."""

import argparse
import os
import time
from collections.abc import Callable

from juno import config
from juno.adapters import cardjob, gate, ledgerio
from juno.cmd import write
from juno.core import cardfmt, cardsettle, compact, vaultfmt
from juno.core.errors import Refused
from juno.core.ledger import Segment

ARCHIVE = "archive/handoffs/{month}.md"
Found = dict[str, list[cardsettle.Span]]
Build = Callable[[config.Config, argparse.Namespace, dict[str, object], str], str]


def handoffs(cfg: config.Config, seg8: str) -> tuple[Found, dict[str, str]]:
    found: Found = {}
    texts: dict[str, str] = {}
    try:
        listed = os.listdir(os.path.join(cfg.vault, "handoffs"))
        names = sorted(n for n in listed if n.endswith(".md"))
    except OSError:
        names = []
    for name in names:
        rel = f"handoffs/{name}"
        text = gate.read(cfg, rel)
        hits = cardsettle.spans(text, seg8)
        if hits:
            found[rel], texts[rel] = hits, text
    return found, texts


def check_segment(cfg: config.Config, seg: Segment, seg8: str) -> None:
    if not cardsettle.SEG8_RE.match(seg8):
        raise Refused("a segment id is 8 lowercase hex characters, e.g. 3ac07d49")
    if seg.sid[:8] == seg8:
        raise Refused("that is this session's segment: run `juno card` without --segment")
    sids = [s for s in ledgerio.ledger_ids(cfg) if s.startswith(seg8)]
    if len(sids) > 1:
        raise Refused(f"segment {seg8} matches {len(sids)} sessions")
    old = ledgerio.fold(cfg, sids[0]) if sids else None
    if old and (cardjob.job_active(cfg, old.sid) or cardjob.late_transcript(cfg, old)):
        raise Refused(f"segment {seg8} still has a card job or a late run pending; retry later")


def stubs_only(cfg: config.Config, seg: Segment, seg8: str) -> tuple[Found, dict[str, str]]:
    check_segment(cfg, seg, seg8)
    found, texts = handoffs(cfg, seg8)
    if not found:
        raise Refused(f"no card of segment {seg8} in handoffs/")
    real = [rel for rel, hits in found.items() if not all(s.stub for s in hits)]
    if real:
        raise Refused(f"segment {seg8} has a real card in {', '.join(real)}; only stubs go")
    return found, texts


def archived(cfg: config.Config, moved: dict[str, list[str]]) -> tuple[str, str]:
    """The month's private handoff archive with the removed blocks appended."""
    month = time.strftime("%Y-%m")
    rel = ARCHIVE.format(month=month)
    text = compact.ensure_private(gate.read(cfg, rel), f"Archive {month}")
    for source, blocks in moved.items():
        text = compact.append_section(text, source.rsplit("/", 1)[-1], blocks)
    return rel, text


def commit_all(
    cfg: config.Config,
    seg: Segment,
    verb: str,
    edits: dict[str, str],
    archive: tuple[str, str] | None,
    evidence: str,
) -> gate.Result:
    """Write every file; the later writes link to the first so one `juno undo` reverts all."""
    first: gate.Result | None = None
    for rel, new in edits.items():
        extra = {"link": first.id} if first else None
        res = gate.commit(cfg, seg, verb, rel, new, evidence, extra=extra, run_cap=first is None)
        first = first or res
    if archive:
        extra = {"link": first.id} if first else None
        gate.commit(cfg, seg, verb + "-archive", *archive, evidence, extra=extra, run_cap=False)
    assert first is not None
    return first


def replace(cfg: config.Config, args: argparse.Namespace, build: Build) -> gate.Result:
    seg = write.guard(cfg)
    evidence = gate.anchor(cfg, seg, args.evidence, user_only=True)
    if args.tickets or args.repos:
        raise Refused("--tickets and --repos come from the stub when --segment is given")
    seg8 = args.segment
    with write.locked(cfg):
        found, texts = stubs_only(cfg, seg, seg8)
        edits: dict[str, str] = {}
        for rel, hits in found.items():
            lead = hits[0]
            block = texts[rel][lead.start : lead.end]
            target: dict[str, object] = {
                "key": lead.key,
                "repos": vaultfmt.items(vaultfmt.card_field(block, "Repos")),
                "tickets": vaultfmt.items(vaultfmt.card_field(block, "Tickets")),
            }
            card = build(cfg, args, target, seg8)
            placed = cardfmt.place_card(texts[rel], seg8, card, rel.rsplit("/", 1)[-1][:-3])
            edits[rel] = cardsettle.cut(placed, cardsettle.spans(placed, seg8)[1:])
        return commit_all(cfg, seg, "card", edits, None, evidence)


def dismiss(cfg: config.Config, args: argparse.Namespace) -> gate.Result:
    seg = write.guard(cfg)
    evidence = gate.anchor(cfg, seg, args.evidence, user_only=False)
    if len(args.lines) != 2:
        raise Refused("usage: juno card dismiss <segment> --evidence E")
    seg8 = args.lines[1]
    with write.locked(cfg):
        found, texts = stubs_only(cfg, seg, seg8)
        edits = {rel: cardsettle.cut(texts[rel], hits) for rel, hits in found.items()}
        moved = {rel: [s.text for s in hits] for rel, hits in found.items()}
        return commit_all(cfg, seg, "card-dismiss", edits, archived(cfg, moved), evidence)


def merge(cfg: config.Config, args: argparse.Namespace) -> gate.Result:
    seg = write.guard(cfg)
    evidence = gate.anchor(cfg, seg, args.evidence, user_only=False)
    if len(args.lines) != 3:
        raise Refused("usage: juno card merge <handoff file> <segment> --evidence E")
    name = args.lines[1].removeprefix("handoffs/").removesuffix(".md")
    seg8 = args.lines[2]
    if not cardsettle.SEG8_RE.match(seg8):
        raise Refused("a segment id is 8 lowercase hex characters, e.g. 3ac07d49")
    rel = f"handoffs/{name}.md"
    with write.locked(cfg):
        text = gate.read(cfg, rel)
        hits = cardsettle.spans(text, seg8)
        if len(hits) < 2:
            raise Refused(f"{rel} has {len(hits)} card of segment {seg8}; nothing to merge")
        if any(s.stub for s in hits):
            raise Refused("a stub is among them: settle it with `juno card dismiss` first")
        merged = cardsettle.merge([s.text for s in hits], seg8, gate.prefixes(cfg))
        first = hits[0]
        tail = "\n" if first.end < len(text) else ""
        new = text[: first.start] + merged + tail + text[first.end :]
        new = cardsettle.cut(new, cardsettle.spans(new, seg8)[1:])
        moved = {rel: [s.text for s in hits[1:]]}
        return commit_all(cfg, seg, "card-merge", {rel: new}, archived(cfg, moved), evidence)
