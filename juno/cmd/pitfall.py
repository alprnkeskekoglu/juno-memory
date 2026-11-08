import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import gate
from juno.cmd import gateio, write
from juno.core import topics, writegate
from juno.core.errors import Refused

REL = "pitfalls.md"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--triggers", help="comma separated trigger words (omit for a general one)")
    gateio.evidence_option(parser)
    parser.add_argument("text", help="the recurring mistake and the correct behaviour")


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    text = gate.scan_line(cfg, args.text, "pitfall")
    gate.anchor(cfg, seg, args.evidence, user_only=False)
    evidence = gate.scan_line(cfg, args.evidence, "evidence")
    found = topics.split_triggers(args.triggers, write.preset(cfg))
    for trigger in found:
        problem = topics.trigger_problem(trigger)
        if problem:
            raise Refused(problem)
    shown = ", ".join(found[: topics.MAX_TRIGGERS]) or "-"
    line = f"- {text} · triggers: {shown} · {evidence}; segment {seg.sid[:8]} · {write.today()}"
    with write.locked(cfg):
        new, _ = writegate.add_item(
            gate.read(cfg, REL), line, write.preset(cfg), base="# Pitfalls\n\n"
        )
        res = gate.commit(cfg, seg, "pitfall", REL, new, evidence)
    return gateio.report(res, "pitfall written to")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
