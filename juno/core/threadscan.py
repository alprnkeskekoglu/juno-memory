import re
import time
from collections.abc import Callable

from juno.core import vaultfmt

REF_RE = re.compile(r"(?<![\w/-])(?:([A-Za-z0-9][A-Za-z0-9._-]{0,79})#|PR\s*#?)(\d{1,7})\b")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SINCE_RE = re.compile(r"· since (\d{4}-\d{2}-\d{2})\s*$")
LIST_RE = re.compile(r"^\[(.*)\]$")
TERMINAL = ("MERGED", "CLOSED")
TASK_REPOS = 3
CLOSED_TASK = ("done", "dropped", "archived")

Lookup = Callable[[str, str], dict[str, str] | None]
Ref = tuple[tuple[list[str], str | None], str]


def thread_lines(text: str) -> list[str]:
    return [row.rstrip() for row in text.splitlines() if vaultfmt.THREAD_RE.match(row.rstrip())]


def parse(line: str) -> tuple[list[str], str, str | None]:
    match = vaultfmt.THREAD_RE.match(line)
    assert match is not None
    tags = [t.strip() for t in match.group(1).split(",") if t.strip()]
    since = SINCE_RE.search(line)
    return tags, match.group(2), since.group(1) if since else None


def task_repos(raw: str) -> list[str]:
    match = LIST_RE.match(raw)
    names = [n.strip().strip("'\"") for n in (match.group(1) if match else raw).split(",")]
    return [n for n in names if NAME_RE.match(n)][:TASK_REPOS]


def ticket_signals(
    tags: list[str], is_ticket: Callable[[str], bool], status_of: Callable[[str], str]
) -> list[str]:
    found = []
    for tag in tags:
        if is_ticket(tag):
            status = status_of(tag).strip().casefold()
            if status in CLOSED_TASK:
                found.append(f"ticket {tag.upper()} is {status}")
    return found


def age_signal(since: str | None, now: float, days: int) -> str | None:
    try:
        start = time.mktime(time.strptime(since, "%Y-%m-%d")) if since else None
    except ValueError:
        start = None
    if not days or start is None or now - start < days * 86400:
        return None
    return f"open for {int((now - start) // 86400)} days (since {since})"


def refs(
    text: str,
    tags: list[str],
    is_ticket: Callable[[str], bool],
    repos_of: Callable[[str], list[str]],
) -> list[Ref]:
    found: list[Ref] = []
    for repo, number in REF_RE.findall(text):
        if repo:
            pair: tuple[list[str], str | None] = ([repo], None)
        else:
            ticket = next((t for t in tags if is_ticket(t)), None)
            repos = repos_of(ticket) if ticket else [t for t in tags if NAME_RE.match(t)][:1]
            pair = (repos, ticket)
        if pair[0] and (pair, number) not in found:
            found.append((pair, number))
    return found


def pr_signals(found: list[Ref], lookup: Lookup) -> list[str]:
    signals: list[str] = []
    for (repos, ticket), number in found:
        hit: tuple[str, str] | None = None
        for repo in repos:
            pr = lookup(repo, number)
            if (
                pr
                and pr.get("state")
                and (ticket is None or ticket.casefold() in pr.get("title", "").casefold())
            ):
                hit = (repo, pr["state"])
                break
        if hit is None or hit[1] not in TERMINAL:
            return []
        signals.append(f"PR {hit[0]}#{number} {hit[1].lower()}")
    return signals
