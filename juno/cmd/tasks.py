import argparse
import json
from collections.abc import Mapping

from juno import config
from juno.adapters import repos, taskio
from juno.cmd.common import say


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--status", help="only tasks with this status")
    parser.add_argument("--repo", help="only tasks that list this repo")
    parser.add_argument("--json", action="store_true")


def line(row: dict[str, object]) -> str:
    repo_list = row["repos"]
    names = ", ".join(repo_list) if isinstance(repo_list, list) else ""
    return f"{row['key']} · {row['status'] or '-'} · {row['title'] or '-'} · {names or '-'}"


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = config.load(env)
    if not repos.tickets_from(cfg).enabled:
        say("jira not configured")
        return 0
    wanted_status = (args.status or "").strip().casefold().replace(" ", "-")
    wanted_repo = (args.repo or "").casefold()
    rows = [
        r
        for r in taskio.rows(cfg)
        if (not wanted_status or r["status"] == wanted_status)
        and (
            not wanted_repo
            or (isinstance(r["repos"], list) and wanted_repo in (x.casefold() for x in r["repos"]))
        )
    ]
    if args.json:
        say(json.dumps({"tasks": rows}, ensure_ascii=False))
    else:
        say("\n".join(line(r) for r in rows) or "no tasks")
    return 0
