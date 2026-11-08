import datetime
import re
from collections.abc import Callable
from typing import Any

from juno.core import vaultfmt

SCAN_DIRS = (("knowledge", "knowledge"), ("projects", "profile"))
GIT_BUDGET_SECONDS = 10.0
MAX_COMMITS = 100
MAX_TOKENS = 40
CACHE_VERSION = 1
BACKTICK_RE = re.compile(r"`([^`\n]{1,200})`")
PATH_RE = re.compile(r"[A-Za-z0-9_.@][A-Za-z0-9_.@/-]*")
EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?(?:^[ \t]*\1[ \t]*$|\Z)", re.M | re.S)

Entry = dict[str, Any]


def as_list(value: str | list[str] | None) -> list[str]:
    if isinstance(value, list):
        return [v.strip().strip("'\"") for v in value if v.strip()]
    text = (value or "").strip().strip("[]")
    return [v.strip().strip("'\"") for v in text.split(",") if v.strip()]


def parse_date(value: str | list[str] | None) -> datetime.date | None:
    text = " ".join(as_list(value))
    if not DATE_RE.fullmatch(text):
        return None
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        return None


def baseline(fields: vaultfmt.Fields, mtime: float) -> tuple[datetime.date, str]:
    for name in ("verified", "updated"):
        found = parse_date(fields.get(name))
        if found:
            return found, name
    return datetime.date.fromtimestamp(mtime), "mtime"


def path_tokens(body: str) -> list[str]:
    found: list[str] = []
    for match in BACKTICK_RE.finditer(FENCE_RE.sub("", body)):
        token = match.group(1).strip().rstrip(".,;:")
        parts = token.split("/")
        if not PATH_RE.fullmatch(token) or ".." in parts or "." in parts:
            continue
        if ("/" in token or EXT_RE.search(token)) and token not in found:
            found.append(token)
    return found[:MAX_TOKENS]


def file_repos(kind: str, fields: vaultfmt.Fields, name: str) -> list[str]:
    repos = as_list(fields.get("repos" if kind == "knowledge" else "repo"))
    if kind == "profile" and not repos:
        repos = [name[:-3]]
    return [r for r in repos if REPO_RE.fullmatch(r)]


def candidates(
    token: str, repos: list[str], repo_known: Callable[[str], bool]
) -> list[tuple[str, str]]:
    first, _, rest = token.partition("/")
    if rest.strip("/") and REPO_RE.fullmatch(first) and (first in repos or repo_known(first)):
        return [(first, rest)]
    return [(repo, token) for repo in repos]


def new_entry(
    kind: str, relpath: str, since: datetime.date, source: str, age: int, days: int, sig: list[int]
) -> Entry:
    return {
        "file": relpath,
        "kind": kind,
        "baseline": since.isoformat(),
        "baseline_source": source,
        "age_days": age,
        "aged": age > days,
        "drift": [],
        "missing_paths": [],
        "missing_repos": [],
        "unchecked": [],
        "sig": sig,
    }


def finish_entry(entry: Entry) -> Entry:
    entry["stale"] = entry["aged"] or bool(entry["drift"])
    return entry


def report_of(
    days: int, today: datetime.date, entries: list[Entry], exhausted: bool, seconds: float
) -> dict[str, Any]:
    return {
        "days": days,
        "today": today.isoformat(),
        "entries": entries,
        "stale": sum(1 for e in entries if e["stale"]),
        "budget_exhausted": exhausted,
        "git_seconds": round(seconds, 2),
    }


def cache_of(report: dict[str, Any], now: int) -> dict[str, Any]:
    files = {
        e["file"]: {
            "stale": e["stale"],
            "since": e["baseline"],
            "sig": e["sig"],
            "aged": e["aged"],
            "drift": sum(d["commits"] for d in e["drift"]),
        }
        for e in report["entries"]
    }
    return {
        "v": CACHE_VERSION,
        "generated": now,
        "days": report["days"],
        "partial": report["budget_exhausted"],
        "files": files,
    }


def entry_lines(entry: Entry) -> list[str]:
    reasons = [f"{entry['age_days']} days old"] + (["drift"] if entry["drift"] else [])
    lines = [
        f"{entry['file']} · baseline {entry['baseline']} ({entry['baseline_source']}) "
        f"· {', '.join(reasons)}"
    ]
    for d in entry["drift"]:
        plus = "+" if d["commits"] >= MAX_COMMITS else ""
        lines.append(
            f"  drift: {d['repo']} {d['path']} "
            f"({d['commits']}{plus} commits since {entry['baseline']})"
        )
    if entry["missing_paths"]:
        lines.append("  not found: " + ", ".join(entry["missing_paths"]))
    if entry["missing_repos"]:
        lines.append("  repos not checked out: " + ", ".join(entry["missing_repos"]))
    if entry["unchecked"]:
        lines.append("  git not checked: " + ", ".join(entry["unchecked"]))
    return lines


def render(report: dict[str, Any]) -> str:
    lines: list[str] = []
    for entry in report["entries"]:
        if entry["stale"]:
            lines += entry_lines(entry)
    lines.append(
        f"{report['stale']} of {len(report['entries'])} knowledge/profile files may be stale "
        f"(older than {report['days']} days or code changed since the baseline); "
        "set `verified: YYYY-MM-DD` after re-checking one."
    )
    if report["budget_exhausted"]:
        lines.append(
            f"git budget of {GIT_BUDGET_SECONDS:.0f} s used up; some paths were not checked."
        )
    return "\n".join(lines)
