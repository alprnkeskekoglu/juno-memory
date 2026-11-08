import argparse
import json
from collections.abc import Mapping

from juno import config
from juno.adapters import schedule
from juno.cmd import write
from juno.cmd.common import say
from juno.core.errors import Refused

JOBS = ("stale-weekly",)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    for name, text in (
        ("install", "install and load the schedule (needs [stale] auto_fix = true)"),
        ("remove", "unload the schedule and delete its files"),
        ("status", "show whether it is loaded, the last run and the next fire time"),
    ):
        action = sub.add_parser(name, help=text)
        action.add_argument("job", nargs="?", default=JOBS[0], choices=JOBS)
    sub.choices["status"].add_argument("--json", action="store_true")


def render_status(found: dict[str, object]) -> list[str]:
    last = found["last"]
    lines = [
        f"schedule: installed {found['installed']}, loaded {found['loaded']}, "
        f"[stale] auto_fix {found['auto_fix']}"
    ]
    if isinstance(last, dict):
        detail = (
            f"{last['fixed']} fixed, {last['archived']} archived"
            if last["status"] == "ok"
            else str(last.get("reason") or "")
        )
        lines.append(f"schedule: last run {last['day']} {last['status']} {detail}".rstrip())
    else:
        lines.append("schedule: no run yet")
    if "next" in found:
        lines.append(f"schedule: next fire {found['next']}")
    return lines


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = config.load(env)
    try:
        if args.action == "install":
            lines = schedule.install(cfg)
        elif args.action == "remove":
            lines = schedule.remove(cfg)
        else:
            found = schedule.status(cfg)
            if args.json:
                print(json.dumps(found, ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            lines = render_status(found)
    except Refused as error:
        say(f"Juno: refused: {error}")
        return write.REFUSED
    for line in lines:
        say(line)
    return 0
