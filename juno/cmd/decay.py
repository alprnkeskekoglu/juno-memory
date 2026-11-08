import argparse
import datetime
import json
import os
import sys
import time
from collections.abc import Mapping

from juno import config
from juno.adapters import store, usageio, vaultview
from juno.core import decay, vaultfmt

SUBDIRS = ("knowledge", "procedures", "notes", "ideas")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--days", type=int, help="window (default: [decay] window_days)")
    parser.add_argument("--json", action="store_true")


def created_of(fields: vaultfmt.Fields, path: str) -> float:
    raw = fields.get("created")
    if isinstance(raw, str):
        try:
            day = datetime.date.fromisoformat(raw.strip()[:10])
            return datetime.datetime.combine(day, datetime.time()).timestamp()
        except ValueError:
            pass
    return os.path.getmtime(path)


def scan(cfg: config.Config) -> dict[str, float]:
    retired = vaultview.read_retired(cfg.state)
    found: dict[str, float] = {}
    for sub in SUBDIRS:
        for base, dirs, names in os.walk(os.path.join(cfg.vault, sub)):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(names):
                path = os.path.join(base, name)
                if not name.endswith(".md") or name.startswith(".") or os.path.islink(path):
                    continue
                rel = os.path.relpath(path, cfg.vault).replace(os.sep, "/")
                text = store.read_text(path)
                if vaultfmt.hidden_reason(rel, text, retired) is None:
                    found[rel] = created_of(vaultfmt.parse_frontmatter(text)[0], path)
    return found


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = config.load(env)
    if not os.path.isdir(cfg.vault):
        sys.stderr.write(f"Juno: vault not found at {cfg.vault}\n")
        return 2
    days = cfg.get_int("decay", "window_days") if args.days is None else args.days
    if days < 1:
        sys.stderr.write("Juno: --days must be at least 1\n")
        return 2
    result = decay.report(scan(cfg), usageio.read_rows(cfg.state), time.time(), days)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(decay.render(result))
    return 0
