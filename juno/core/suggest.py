import re
from typing import Any

from juno.core import privacy
from juno.core.backlog import LOG_CHARS, SUGGEST_STATUSES
from juno.core.digest import clip
from juno.core.redact import redact
from juno.core.textchecks import SECOND_PERSON_RE, anchored, text_field

Row = dict[str, Any]

SUGGESTIONS_PER_RUN = 3
STATE_ITEMS = 6
STATE_CHARS = 800


def mentioned(word: str, haystack: str) -> bool:
    return (
        re.search(r"(?<![\w-])" + re.escape(word.casefold()) + r"(?![\w-])", haystack) is not None
    )


def relevant_items(rows: list[Row], repos: list[str], tickets: list[str], digest: str) -> list[Row]:
    haystack = (digest or "").casefold()
    tags = {t.casefold() for t in [*repos, *tickets]}
    picked = []
    for row in rows:
        words = [row["slug"], *row["tickets"], *row["repos"]]
        if any(w.casefold() in tags or mentioned(w, haystack) for w in words):
            picked.append(row)
    return picked[:STATE_ITEMS]


def state_text(items: list[Row]) -> str:
    if not items:
        return ""
    lines = ["Open backlog items mentioned in this segment (slug · title · status · tickets):"]
    lines += [
        f"- {r['slug']} · {r['title']} · {r['status']} · {', '.join(r['tickets']) or '-'}"
        for r in items
    ]
    return clip(redact("\n".join(lines)), STATE_CHARS)


def instruction(items: list[Row]) -> list[str]:
    if not items:
        return []
    return [
        '- "backlog": at most 3 {"slug", "log", "status"} for the open backlog items listed in '
        'the vault state that this segment moved forward. "slug": one of those slugs. "log": one '
        "neutral English line, 10-200 chars, no `·`, naming a concrete anchor from the digest "
        '(ticket key, file, command, commit or PR URL). "status": optional, only "done", '
        '"waiting" or "doing" when the digest shows it. The run only proposes these; the user '
        "decides."
    ]


def check_suggestion(entry: object, listed: dict[str, Row], digest: str) -> Row | None:
    if not isinstance(entry, dict):
        return None
    slug, log = text_field(entry.get("slug")), text_field(entry.get("log"))
    status = text_field(entry.get("status")) or None
    if slug not in listed or not LOG_CHARS[0] <= len(log) <= LOG_CHARS[1] or "·" in log:
        return None
    if privacy.scan(log) or SECOND_PERSON_RE.search(log) or not anchored(log, digest):
        return None
    if status is not None and status not in SUGGEST_STATUSES:
        return None
    if status == listed[slug]["status"]:
        status = None
    return {"slug": slug, "log": log, "new_status": status, "kind": ""}


def ticket_done(listed: dict[str, Row], picked: list[Row], done_tickets: set[str]) -> list[Row]:
    extra: list[Row] = []
    for slug, row in listed.items():
        if any(p["slug"] == slug and p["new_status"] == "done" for p in picked):
            continue
        key = next((k for k in row["tickets"] if k in done_tickets), None)
        if key:
            log = f"Ticket {key} is done (tasks/{key}.md status: done)"
            extra.append({"slug": slug, "log": log, "new_status": "done", "kind": ""})
    return extra


def pick(
    entries: list[object], listed: dict[str, Row], digest: str, done_tickets: set[str]
) -> tuple[list[Row], int]:
    dropped = max(0, len(entries) - SUGGESTIONS_PER_RUN)
    picked: list[Row] = []
    for entry in entries[:SUGGESTIONS_PER_RUN]:
        checked = check_suggestion(entry, listed, digest)
        if checked is None or any(p["slug"] == checked["slug"] for p in picked):
            dropped += 1
        else:
            picked.append(checked)
    picked += ticket_done(listed, picked, done_tickets)
    return picked, dropped
