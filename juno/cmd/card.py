import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import gate
from juno.cmd import gateio, write
from juno.core import cardfmt, routing, writegate
from juno.core.errors import Refused

MIN_LINES, MAX_LINES = 2, 5
LABEL_MAX = 40


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--label", default="work", help="short label for the heading")
    parser.add_argument("--learned", required=True, help="one sentence, or none")
    parser.add_argument("--decision", help="one line, only if the user decided something")
    parser.add_argument("--links", help="wikilinks, e.g. '[[repo]], [[KEY]]'")
    parser.add_argument("lines", nargs="+", help="2-5 lines: done, where it stopped, next")


def build(cfg: config.Config, args: argparse.Namespace, target: routing.Target, seg_id: str) -> str:
    if not MIN_LINES <= len(args.lines) <= MAX_LINES:
        raise Refused(f"a card has {MIN_LINES}-{MAX_LINES} lines")
    label = gate.scan_text(cfg, args.label, "label")
    if "·" in label or "\n" in label or len(label) > LABEL_MAX:
        raise Refused(f"label must be one line of at most {LABEL_MAX} characters, without '·'")
    body = [gate.scan_text(cfg, x, "card line") for x in args.lines]
    out = [
        f"## {routing.local_stamp()} · {target['key']} · {label} · {seg_id}",
        "Repos: " + (", ".join(target["repos"]) or "-"),
        "Tickets: " + (", ".join(target["tickets"]) or "-"),
        *body,
    ]
    if args.links:
        out.append("Links: " + gate.scan_text(cfg, args.links, "links"))
    if args.decision:
        out.append("**Decision:** " + gate.scan_text(cfg, args.decision, "decision"))
    out.append("Learned: " + gate.scan_text(cfg, args.learned, "learned"))
    card = "\n".join(out) + "\n"
    writegate.check_length(card, "card")
    try:
        card = cardfmt.validate_card(
            card, seg_id, str(target["key"]), routing.local_stamp(), gate.prefixes(cfg)
        )
    except cardfmt.Failed as exc:
        raise Refused(str(exc)) from None
    return cardfmt.fix_links(card)[0].rstrip("\n") + "\n"


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    target = routing.card_targets(seg)[0]
    card = build(cfg, args, target, seg.sid[:8])
    rel = str(target["path"])
    name = rel.rsplit("/", 1)[-1][:-3]
    with write.locked(cfg):
        new = cardfmt.place_card(gate.read(cfg, rel) or "", seg.sid[:8], card, name)
        res = gate.commit(cfg, seg, "card", rel, new, f"segment {seg.sid[:8]}", card=True)
    return gateio.report(res, "card written to")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
