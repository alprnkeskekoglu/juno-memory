import argparse
import json
import os
import sys
from collections.abc import Mapping

from juno import config
from juno.adapters import stalescan
from juno.core import stale


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--days", type=int, help="age limit (default: [thresholds] stale_days)")
    parser.add_argument("--json", action="store_true")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = config.load(env)
    if not os.path.isdir(cfg.vault):
        sys.stderr.write(f"Juno: vault not found at {cfg.vault}\n")
        return 2
    days = cfg.get_int("thresholds", "stale_days") if args.days is None else args.days
    if days < 1:
        sys.stderr.write("Juno: --days must be at least 1\n")
        return 2
    report = stalescan.run(cfg, days)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(stale.render(report))
    return 0
