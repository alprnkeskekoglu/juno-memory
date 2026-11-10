import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import gate
from juno.cmd import fix, gateio, write
from juno.core import selfmodel, writegate
from juno.core.errors import Refused

FILES = ("values", "decisions", "communication", "tensions")
QUOTE_MAX = 200


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file", required=True, choices=FILES)
    gateio.evidence_option(parser, "your own words, verbatim from this segment")
    parser.add_argument("--supersedes", help="<self/file>.md:<line number> this line reverses")
    parser.add_argument("--was", help="a fragment of the line --supersedes points at")
    parser.add_argument("text", help="what the user's words show about them")


def supersede(cfg: config.Config, args: argparse.Namespace, rel: str, today: str) -> str:
    if not args.was:
        raise Refused("--supersedes needs --was (a fragment of the line as it is now)")
    target, lines, index = fix.locate(cfg, args.supersedes, args.was, forget=False)
    if target != rel:
        raise Refused(f"--supersedes must point into {rel} (the --file of the new line)")
    if not lines[index].startswith("- ") or selfmodel.parse(lines[index]).superseded:
        raise Refused("that line is not an active self line")
    lines[index] = selfmodel.mark_superseded(lines[index], today)
    return writegate.join(lines)


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    text = gate.scan_line(cfg, args.text, "self line")
    pid = gate.anchor(cfg, seg, args.evidence, user_only=True).split()[-1]
    quote = gate.scan_evidence(cfg, args.evidence, "evidence")
    if len(quote) > QUOTE_MAX or "·" in quote:
        raise Refused(f"evidence must be a verbatim part of at most {QUOTE_MAX} characters")
    rel, today = f"self/{args.file}.md", write.today()
    line = f'- {text} · {today} · src: user {pid} · "{quote}"'
    with write.locked(cfg):
        old = supersede(cfg, args, rel, today) if args.supersedes else gate.read(cfg, rel)
        base = f"# {args.file.capitalize()}\n\n"
        new, _ = selfmodel.record(old, line, write.preset(cfg), today, base)
        res = gate.commit(cfg, seg, "self", rel, new, f"user {pid}")
    return gateio.report(res, "self line written to")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
