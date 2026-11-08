import argparse
import json
import os
import sys
import time
from collections.abc import Mapping

from juno import config
from juno.adapters import armsio, reportio
from juno.cmd import arm, decay
from juno.core import isoweek, report, reportfmt
from juno.core.errors import Refused


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--week", default="last", help="YYYY-Www or last (default)")
    parser.add_argument("--json", action="store_true")


def described_arms(cfg: config.Config) -> list[dict[str, object]]:
    rows = []
    for name in armsio.names(cfg):
        try:
            rows.append(arm.describe(cfg, name))
        except Refused, KeyError, TypeError:
            continue
    return rows


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = config.load(env)
    if not os.path.isdir(cfg.vault):
        sys.stderr.write(f"Juno: vault not found at {cfg.vault}\n")
        return 2
    now = time.time()
    try:
        label = isoweek.last_week(now) if args.week == "last" else args.week
        isoweek.bounds(label)
        with reportio.locked(cfg):
            raw = reportio.collect(cfg, label, now, described_arms(cfg), decay.scan(cfg))
            data = report.build(raw)
            rel = reportio.write(cfg, label, reportfmt.render(data))
    except Refused as error:
        sys.stderr.write(f"Juno: refused: {error}\n")
        return 2
    except TimeoutError:
        sys.stderr.write("Juno: refused: another report is running\n")
        return 2
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        print(f"Juno: weekly report {label} written to {rel}")
    return 0
