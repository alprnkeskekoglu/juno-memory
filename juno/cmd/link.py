import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import gate
from juno.cmd import gateio, linkargs, write
from juno.core import linkline
from juno.core.errors import Refused

TOPS = ("notes", "knowledge")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("ref", help="notes/<file>.md or knowledge/<file>.md, an existing file")
    linkargs.add_option(parser)
    gateio.evidence_option(parser)


def check_ref(ref: str) -> None:
    top, _, name = ref.partition("/")
    if top not in TOPS or not name.endswith(".md") or "/" in name or "\n" in ref:
        raise Refused("juno link works on notes/<file>.md and knowledge/<file>.md only")


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    check_ref(args.ref)
    if not args.link:
        raise Refused('link needs at least one --link "<target>:<reason>"')
    seg = write.guard(cfg)
    evidence = gate.anchor(cfg, seg, args.evidence, user_only=False)
    specs, canon = linkargs.prepare(cfg, args.link)
    if any(canon(target) == args.ref for target, _ in specs):
        raise Refused("a file cannot link to itself")
    with write.locked(cfg):
        old = gate.read(cfg, args.ref)
        if not old:
            raise Refused(f"{args.ref} does not exist (link adds to an existing file)")
        res = gate.commit(
            cfg, seg, "link", args.ref, linkline.with_links(old, specs, canon), evidence
        )
    return gateio.report(res, "links written to")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
