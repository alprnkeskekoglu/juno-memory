import argparse
import datetime
import json
import os
import sys
from collections.abc import Mapping
from typing import Any

from juno import config
from juno.adapters import index, indexquery


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("query")
    parser.add_argument("--repo")
    parser.add_argument("--ticket")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--since", metavar="YYYY-MM-DD", help="only passages dated on or after")
    parser.add_argument("--until", metavar="YYYY-MM-DD", help="only passages dated on or before")
    parser.add_argument("--archive", action="store_true", help="include archive/ (flagged)")
    parser.add_argument("--json", action="store_true")


def render(hits: list[dict[str, Any]], corrections: dict[str, list[str]]) -> str:
    lines = []
    for tok, words in corrections.items():
        quoted = ", ".join(f'"{w}"' for w in words)
        lines.append(f'(searched {quoted} for "{tok}")')
    for item in hits:
        flag = (" [archive]" if item.get("archive") else "") + (
            " [superseded]" if item.get("superseded") else ""
        )
        lines.append(f"{item['relpath']}{flag} · {item['heading'] or '-'} · {item['day']}")
        lines.append("  " + item["snippet"])
    return "\n".join(lines) or "no results"


def bad_day(value: str | None) -> bool:
    if value is None:
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return True
    return len(value) != 10


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = config.load(env)
    if not os.path.isdir(cfg.vault):
        sys.stderr.write(f"Juno: vault not found at {cfg.vault}\n")
        return 2
    if args.limit < 1:
        sys.stderr.write("Juno: --limit must be at least 1\n")
        return 2
    for name in ("since", "until"):
        if bad_day(getattr(args, name)):
            sys.stderr.write(f"Juno: --{name} must be a date like 2026-10-31\n")
            return 2
    if args.since and args.until and args.since > args.until:
        sys.stderr.write("Juno: --since is after --until\n")
        return 2
    try:
        db, stats = index.sync(cfg)
    except TimeoutError:
        sys.stderr.write("Juno: the index is busy; retry in a moment\n")
        return 2
    corrections: dict[str, list[str]] = {}
    hits = indexquery.search(
        db,
        args.query,
        repo=args.repo,
        ticket=args.ticket,
        limit=args.limit,
        corrections=corrections,
        spread=True,
        archive=args.archive,
        preset=cfg.get_str("language", "preset"),
        ticket_files=bool(cfg.get_list("jira", "prefixes")),
        keys_weight=cfg.get_float("keys", "weight"),
        since=args.since,
        until=args.until,
    )
    if args.json:
        data: dict[str, Any] = {"query": args.query, "results": hits, "index": stats}
        if corrections:
            data["corrections"] = corrections
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render(hits, corrections))
    return 0
