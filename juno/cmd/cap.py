import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import gate, ledgerio, writelog
from juno.cmd import gateio, write
from juno.core.errors import Refused

MAX_RAISE = 100


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    up = sub.add_parser("raise", help="raise this segment's write cap after the user approves")
    up.add_argument("--by", type=int, required=True, help=f"extra writes, 1-{MAX_RAISE}")
    gateio.evidence_option(
        up, "your own words approving it, typed or chosen this segment, verbatim (20+ characters)"
    )
    sub.add_parser("show", help="print used/limit for this segment")


def used(cfg: config.Config, sid: str) -> int:
    return writelog.live_count(writelog.read(cfg), sid)


def show(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    print(f"Juno: {used(cfg, seg.sid)} used, limit {gate.run_limit(cfg, seg)}")
    return 0


def raise_cap(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    if not 1 <= args.by <= MAX_RAISE:
        raise Refused(f"--by must be between 1 and {MAX_RAISE}")
    gate.anchor(cfg, seg, args.evidence, user_only=True)
    ledgerio.record(cfg, seg, {"ev": "cap_raise", "by": args.by})
    print(f"Juno: write cap raised by {args.by} to {gate.run_limit(cfg, seg)} for this segment")
    return 0


ACTIONS = {"raise": raise_cap, "show": show}


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return write.execute(args, env, lambda cfg: ACTIONS[args.action](cfg, args))
