import argparse
import datetime
import json
import os
import sys
from collections.abc import Mapping

from juno import config
from juno.adapters import store, vaultview
from juno.core import recap as core
from juno.core import vaultfmt


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--week", action="store_true", help="same as --days 7")
    parser.add_argument("--ticket")
    parser.add_argument("--json", action="store_true")


def visible_files(cfg: config.Config, directory: str, hidden: set[str]) -> dict[str, str]:
    root = os.path.join(cfg.vault, directory)
    found: dict[str, str] = {}
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return found
    for entry in entries:
        if not core.STRICT_NAME_RE.match(entry.name) or not entry.is_file(follow_symlinks=False):
            continue
        rel = f"{directory}/{entry.name}"
        if rel in hidden:
            continue
        text = store.read_text(entry.path)
        if vaultfmt.hidden_reason(rel, text, None) is None:
            found[rel] = text
    return found


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = config.load(env)
    if not os.path.isdir(cfg.vault):
        sys.stderr.write(f"Juno: vault not found at {cfg.vault}\n")
        return 2
    days = 7 if args.week else args.days
    if days < 1:
        sys.stderr.write("Juno: --days must be at least 1\n")
        return 2
    hidden = set(vaultview.sync_retired(cfg).retired)
    text = core.recap(
        visible_files(cfg, "handoffs", hidden),
        visible_files(cfg, "tasks", hidden),
        datetime.date.today(),
        days,
        args.ticket,
        "Juno",
    )
    if args.json:
        data = {"days": days, "ticket": args.ticket, "markdown": text}
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(text.rstrip("\n"))
    return 0
