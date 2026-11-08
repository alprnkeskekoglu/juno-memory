import argparse
import json
from collections.abc import Mapping

from juno import config
from juno.adapters import checks, repos, taskio, vault
from juno.cmd import write
from juno.cmd.common import say
from juno.core import taskfile
from juno.core.errors import Refused

NOT_CONFIGURED = "Juno: jira not configured: set [jira] prefixes in juno.toml"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    put = sub.add_parser("set", help="write tasks/<KEY>.md from data the session's Jira tool gave")
    put.add_argument("key")
    put.add_argument("--title", help="ticket title")
    put.add_argument("--status", help="ticket status, e.g. 'In Review' (stored as in-review)")
    put.add_argument("--url", help="ticket link (default: <base_url>/browse/<KEY>)")
    put.add_argument("--slack-checked", metavar="YYYY-MM-DD", help="day the Slack digest ran")
    put.add_argument("--repo", action="append", default=[], help="repo the ticket touches")
    show = sub.add_parser("show", help="print tasks/<KEY>.md")
    show.add_argument("key")
    show.add_argument("--json", action="store_true")


def fail(message: str) -> int:
    say(f"Juno: {message}")
    return 2


def checked_key(cfg: config.Config, raw: str) -> tuple[str, str | None]:
    tickets = repos.tickets_from(cfg)
    if not tickets.enabled:
        return "", NOT_CONFIGURED
    key = raw.strip().upper()
    if tickets.find(key) != [key]:
        return "", f"Juno: {raw!r} is not a ticket key for [jira] prefixes"
    return key, None


def updates_from(cfg: config.Config, key: str, args: argparse.Namespace) -> taskfile.Updates | str:
    updates: taskfile.Updates = {}
    prefixes = cfg.get_list("jira", "prefixes")
    steps = (
        ("title", args.title, lambda raw: taskfile.clean_title(raw, prefixes)),
        ("status", args.status, taskfile.clean_status),
        ("url", args.url, lambda raw: taskfile.clean_url(raw, prefixes)),
        ("slack_checked", args.slack_checked, taskfile.clean_day),
    )
    for name, raw, clean in steps:
        if raw is None:
            continue
        value, error = clean(raw)
        if error:
            return error
        updates[name] = value
    if args.repo:
        names, error = taskfile.clean_repos(args.repo)
        if error:
            return error
        updates["repos"] = names
    return updates


def run_set(cfg: config.Config, key: str, args: argparse.Namespace) -> int:
    try:
        seg = write.guard(cfg)
    except Refused as error:
        say(checks.refusal_line(str(error)))
        return 2
    if vault.status(cfg.vault) != "juno":
        return fail(f"no Juno vault at {cfg.vault}; run juno init")
    updates = updates_from(cfg, key, args)
    if isinstance(updates, str):
        return fail(updates)
    existing = taskio.read(cfg, key)
    if existing is None and taskio.path(cfg, key) is None:
        return fail(f"{taskio.rel(key)} is outside the vault (symlink)")
    if existing is None:
        if args.title is None or args.status is None:
            return fail(f"{key} has no task file yet; pass --title and --status")
        if "url" not in updates:
            url = taskfile.default_url(cfg.get_str("jira", "base_url"), key)
            if url:
                updates["url"] = url
    elif not updates:
        return fail("nothing to set; pass --title, --status, --url, --slack-checked or --repo")
    try:
        with write.locked(cfg):
            res = taskio.write_fields(cfg, seg, key, updates)
    except (OSError, Refused) as error:
        return fail(f"cannot write {taskio.rel(key)}: {error}")
    verb = "created" if existing is None else "updated"
    suffix = f" (write {res.id}; revert with `juno undo {res.id}`)" if res.id else ""
    say(f"task {key}: {verb} {taskio.rel(key)}{suffix}")
    return 0


def run_show(cfg: config.Config, key: str, as_json: bool) -> int:
    text = taskio.read(cfg, key)
    if text is None:
        return fail(f"no task file for {key}")
    say(json.dumps(taskfile.row(key, text), ensure_ascii=False) if as_json else text.rstrip("\n"))
    return 0


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = config.load(env)
    key, error = checked_key(cfg, args.key)
    if error:
        say(error)
        return 2
    if args.action == "set":
        return run_set(cfg, key, args)
    return run_show(cfg, key, args.json)
