import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import gate
from juno.cmd import gateio, write
from juno.core import selfguard, writegate
from juno.core.errors import Refused

FILES = ("values", "decisions", "communication", "tensions")
QUOTE_MAX = 200


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file", required=True, choices=FILES)
    gateio.evidence_option(parser, "your own words, verbatim from this segment")
    parser.add_argument("text", help="what the user's words show about them")


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    text = gate.scan_line(cfg, args.text, "self line")
    pid = gate.anchor(cfg, seg, args.evidence, user_only=True).split()[-1]
    quote = gate.scan_evidence(cfg, args.evidence, "evidence")
    problem = selfguard.content_problem(text, quote)
    if problem:
        raise Refused(f"self line refused ({problem}): record working behaviour only")
    if len(quote) > QUOTE_MAX or "·" in quote:
        raise Refused(f"evidence must be a verbatim part of at most {QUOTE_MAX} characters")
    rel = f"self/{args.file}.md"
    line = f'- {text} · {write.today()} · src: user {pid} · "{quote}"'
    with write.locked(cfg):
        new, _ = writegate.add_item(
            gate.read(cfg, rel), line, write.preset(cfg), base=f"# {args.file.capitalize()}\n\n"
        )
        res = gate.commit(cfg, seg, "self", rel, new, f"user {pid}")
    return gateio.report(res, "self line written to")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
