import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import gate
from juno.cmd import gateio, write
from juno.core import topics, writegate
from juno.core.errors import Refused

MIN_STEPS, MAX_STEPS = 2, 12


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--slug", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--triggers", help="comma-separated words or commands that inject it")
    parser.add_argument("--repos", help="comma-separated repos it is specific to")
    gateio.evidence_option(parser)
    parser.add_argument("steps", nargs="+", help="one argument per step; rewrites an existing file")


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    slug = writegate.check_slug(args.slug)
    title = gate.scan_line(cfg, args.title, "title")
    if not MIN_STEPS <= len(args.steps) <= MAX_STEPS:
        raise Refused(f"a procedure has {MIN_STEPS}-{MAX_STEPS} steps")
    steps = [gate.scan_line(cfg, s, "step") for s in args.steps]
    triggers = topics.split_triggers(args.triggers, write.preset(cfg))
    for trigger in triggers:
        problem = topics.trigger_problem(trigger)
        if problem:
            raise Refused(problem)
    if len(triggers) > topics.MAX_TRIGGERS:
        raise Refused(f"at most {topics.MAX_TRIGGERS} triggers")
    repos = [r.strip() for r in (args.repos or "").split(",") if r.strip()]
    if not all(topics.KNOWLEDGE_RE.match(r + ".md") for r in repos):
        raise Refused("--repos takes repo names, comma-separated")
    text = topics.procedure_text(title, write.today(), seg.sid[:8], steps, triggers, repos)
    writegate.check_length(text, "procedure")
    evidence = gate.anchor(cfg, seg, args.evidence, user_only=False)
    rel = f"procedures/{slug}.md"
    with write.locked(cfg):
        res = gate.commit(cfg, seg, "procedure", rel, text, evidence)
    return gateio.report(res, "procedure written to")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
