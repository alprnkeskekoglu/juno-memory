"""Session-end step (#36): propose triggers for the knowledge files a session read by hand.

A file without `triggers:` is never injected by a matching call. When the session read one with
Read, Grep or Glob, triggers from its title and body are queued as a candidate; a file that has
triggers gets the user-language form of what the user typed before the read. Nothing is written
to the file until the user accepts the candidate (`juno rule accept`).
"""

import json
from collections import Counter
from typing import Any

from juno import config
from juno.adapters import fs, ledgerio, queues, usageio, vaultview
from juno.adapters.learnkinds.base import Ctx, vault_text
from juno.core import candidates, prompts, topics, triggerprop, vaultfmt, writegate

PER_RUN = 3


def typed_prompts(cfg: config.Config, sid: str) -> list[tuple[float, str]]:
    """(time, text) of each typed prompt of the segment, in order."""
    path = ledgerio.ledger_path(cfg, sid)
    raw = fs.read_bytes(path) if path else None
    found: list[tuple[float, str]] = []
    for line in (raw or b"").decode("utf-8", errors="replace").splitlines():
        try:
            rec: dict[str, Any] = json.loads(line)
        except ValueError:
            continue
        text = str(rec.get("text") or "")
        stamp = rec.get("t")
        if (
            rec.get("ev") == "prompt"
            and rec.get("part", 0) == 0
            and isinstance(stamp, int | float)
            and not prompts.is_synthetic(text)
        ):
            found.append((float(stamp), prompts.user_text(text)))
    return found


def said_before(typed: list[tuple[float, str]], at: float) -> str:
    before = [text for stamp, text in typed if stamp <= at]
    return before[-1] if before else ""


def read_files(ctx: Ctx) -> list[tuple[float, str]]:
    first: dict[str, float] = {}
    for stamp, rel in usageio.read_touches(ctx.cfg.state, ctx.seg.sid) or []:
        if rel.startswith("knowledge/") and topics.KNOWLEDGE_RE.match(rel[len("knowledge/") :]):
            first.setdefault(rel, stamp)
    return [(stamp, rel) for rel, stamp in first.items()]


def vocab(cfg: config.Config) -> triggerprop.Vocab:
    """Ticket prefixes of `[jira] prefixes` and the tokens found in more than a quarter of the
    visible knowledge files. Both proposal paths filter with it."""
    preset = cfg.get_str("language", "preset")
    prefixes = tuple(cfg.get_list("jira", "prefixes"))
    view = vaultview.DiskVault(cfg)
    counts: Counter[str] = Counter()
    total = 0
    for name in view.names("knowledge"):
        rel = f"knowledge/{name}"
        text = view.read(rel)
        if not topics.KNOWLEDGE_RE.match(name) or view.hidden(rel, text):
            continue
        fields, body = vaultfmt.parse_frontmatter(text)
        total += 1
        counts.update(
            triggerprop.tokens(
                str(fields.get("title") or ""), body, preset, triggerprop.Vocab(prefixes)
            )
        )
    return triggerprop.Vocab(prefixes, triggerprop.common_tokens(counts, total))


def proposals(ctx: Ctx) -> list[tuple[str, list[str]]]:
    cfg = ctx.cfg
    preset = cfg.get_str("language", "preset")
    stopwords = cfg.language_list("stopwords")
    retired = vaultview.read_retired(cfg.state)
    typed = typed_prompts(cfg, ctx.seg.sid)
    found: list[tuple[str, list[str]]] = []
    reads = read_files(ctx)
    words = vocab(cfg) if reads else triggerprop.Vocab()
    for stamp, rel in reads:
        text = vault_text(cfg, rel)
        if not text or vaultfmt.hidden_reason(rel, text, retired):
            continue
        fields, body = vaultfmt.parse_frontmatter(text)
        have = topics.field_list(fields.get("triggers"), preset)
        title = str(fields.get("title") or "")
        new = [] if have else triggerprop.propose(title, body, preset, words)
        said = said_before(typed, stamp)
        room = max(topics.MAX_TRIGGERS - len(have) - len(new), 0)
        extra = triggerprop.user_forms(said, f"{title}\n{body}", [*have, *new], preset, stopwords)
        merged = [*new, *extra[:room]]
        if merged:
            found.append((rel, merged))
    return found[:PER_RUN]


def propose(ctx: Ctx) -> int:
    """Queue the proposals as pending rule-queue items of kind `triggers`; return how many."""
    found = proposals(ctx)
    if not found:
        return 0
    preset = ctx.cfg.get_str("language", "preset")

    def change(queue: list[candidates.Item]) -> int:
        queue[:] = candidates.prune(queue, ctx.now)
        return sum(
            candidates.add_triggers(queue, rel, triggers, ctx.seg.sid, ctx.now, preset)
            for rel, triggers in found
        )

    queued: int = queues.update(ctx.cfg, queues.RULES, change)
    return queued


def merged_triggers(cfg: config.Config, text: str, wanted: list[str]) -> list[str]:
    preset = cfg.get_str("language", "preset")
    fields, _ = vaultfmt.parse_frontmatter(text)
    have = topics.field_list(fields.get("triggers"), preset)
    extra = [t for t in topics.split_triggers(",".join(wanted), preset) if t not in have]
    return [*have, *[t for t in extra if not topics.trigger_problem(t)]][: topics.MAX_TRIGGERS]


def with_triggers(text: str, triggers: list[str]) -> str:
    """The file text with its `triggers:` front matter line set (the front matter made if
    the file has none)."""

    value = "[" + ", ".join(triggers) + "]"
    fields, _ = vaultfmt.parse_frontmatter(text)
    if text.startswith("---") and fields:
        return writegate.set_field(text, "triggers", value)
    return writegate.frontmatter({"triggers": value}) + "\n" + text
