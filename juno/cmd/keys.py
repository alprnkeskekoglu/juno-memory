import argparse
import json
import os
import sys
from collections.abc import Mapping
from typing import Any

from juno import config
from juno.adapters import env as envmod
from juno.adapters import keysbackfill
from juno.core import keys as keycore


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    fill = sub.add_parser("backfill", help="key the passages that have no fact keys (model calls)")
    fill.add_argument("--limit", type=int, help="key at most this many passages")
    fill.add_argument("--max-usd", type=float, default=2.0)
    fill.add_argument("--dry-run", action="store_true", help="count the work, call no model")
    status = sub.add_parser("status", help="keyed and total passages")
    for action in (fill, status):
        action.add_argument("--json", action="store_true")


def emit(args: argparse.Namespace, data: dict[str, Any], text: str) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) if args.json else text)


def do_status(args: argparse.Namespace, cfg: config.Config) -> int:
    data = keysbackfill.status(cfg)
    emit(
        args,
        data,
        f"keyed {data['keyed']}/{data['total']} passages; {data['pending']} pending, "
        f"{data['empty']} without usable facts ({data['retry']} to retry)"
        + "".join(f"; {why} {n}" for why, n in sorted(data["reasons"].items())),
    )
    return 0


def do_backfill(args: argparse.Namespace, cfg: config.Config) -> int:
    if envmod.agent_context(cfg.env):
        sys.stderr.write("Juno: refused: agent context\n")
        return 2
    if (args.limit is not None and args.limit < 1) or args.max_usd <= 0:
        sys.stderr.write("Juno: refused: --limit and --max-usd must be positive\n")
        return 2
    if not os.path.isdir(cfg.vault):
        sys.stderr.write(f"Juno: vault not found at {cfg.vault}\n")
        return 2
    if args.dry_run:
        found = keysbackfill.scan(cfg)
        todo = found.todo + found.retry
        todo = todo[: args.limit] if args.limit else todo
        batches = -(-len(todo) // keycore.BATCH)
        data = {"passages": found.total, "pending": len(todo), "batches": batches, "dry_run": True}
        emit(args, data, f"{len(todo)} of {found.total} passages pending in {batches} calls")
        return 0
    try:
        out = keysbackfill.backfill(cfg, args.limit, args.max_usd)
    except TimeoutError:
        sys.stderr.write("Juno: refused: another keys backfill is running\n")
        return 2
    emit(
        args,
        out,
        f"keyed {out['keyed']}, dropped {out['dropped']} in {out['calls']} calls, "
        f"${out['cost_usd']:.2f} spent"
        + (f"; stopped: {out['stopped']}" if out["stopped"] else ""),
    )
    return 1 if out["stopped"] and out["stopped"].startswith("model") else 0


ACTIONS = {"backfill": do_backfill, "status": do_status}


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return ACTIONS[args.action](args, config.load(env))
