import datetime
import re
from typing import Any

from juno.core import backlog, threadscan, vaultfmt

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
SHORT_DATE_RE = re.compile(r"(?<![\d-])(\d{2})-(\d{2})(?![\d-])")
DUE_SOON_DAYS = 3


def days_since(since: str | None, today: str) -> int | None:
    if not since:
        return None
    return (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(since)).days


def day_state(day: str, today: str) -> str:
    return "past" if day < today else "due" if day == today else "future"


def yearless_dates(text: str, anchor: str) -> list[str]:
    found = []
    for match in SHORT_DATE_RE.finditer(text):
        month, number = int(match.group(1)), int(match.group(2))
        try:
            day = datetime.date(int(anchor[:4]), month, number).isoformat()
            if day < anchor:
                day = datetime.date(int(anchor[:4]) + 1, month, number).isoformat()
        except ValueError:
            continue
        found.append(day)
    return found


def thread_group(tags: list[str], ticket: re.Pattern[str] | None) -> str:
    key = next((t for t in tags if ticket and ticket.fullmatch(t)), None)
    return key.upper() if key else tags[0] if tags else "-"


def parse_threads(text: str, today: str, ticket: re.Pattern[str] | None) -> list[dict[str, Any]]:
    items = []
    for number, line in enumerate(text.splitlines(), 1):
        match = vaultfmt.THREAD_RE.match(line.rstrip())
        if not match:
            continue
        tags, body, since = threadscan.parse(line.rstrip())
        dates = DATE_RE.findall(threadscan.SINCE_RE.sub("", body))
        dates += yearless_dates(body, since or today)
        due = max(dates) if dates else None
        items.append(
            {
                "line": number,
                "tag": match.group(1),
                "tags": tags,
                "group": thread_group(tags, ticket),
                "age": days_since(since, today),
                "text": body,
                "since": since,
                "dates": [{"date": d, "state": day_state(d, today)} for d in dates],
                "due": due,
                "due_state": day_state(due, today) if due else None,
            }
        )
    return items


def backlog_rows(files: list[tuple[str, str]]) -> list[dict[str, Any]]:
    return [dict(backlog.item_row(slug, text)) for slug, text in files]


def backlog_order(row: dict[str, Any]) -> tuple[bool, str, str, str, str]:
    return (
        row["status"] in backlog.CLOSED,
        row["priority"],
        row["due"] or "9999-99-99",
        row["added"],
        row["slug"],
    )


def backlog_block(files: list[tuple[str, str]], today: str, limit: int) -> dict[str, Any]:
    loaded = backlog_rows(files)
    rows = [r for r in loaded if not r["problem"] and r["status"] not in backlog.CLOSED]
    rows.sort(key=backlog_order)
    soon = (datetime.date.fromisoformat(today) + datetime.timedelta(days=DUE_SOON_DAYS)).isoformat()
    items = []
    for r in rows:
        due = r["due"]
        items.append(
            {
                **{k: r[k] for k in ("slug", "title", "status", "priority", "due")},
                "overdue": bool(due and due < today),
                "urgent": r["priority"] == "p1" or bool(due and due <= soon),
            }
        )
    return {
        "open": len(rows),
        "invalid": sum(1 for r in loaded if r["problem"]),
        "items": items[:limit],
    }


def parse_cards(text: str, relpath: str) -> list[dict[str, Any]]:
    return [
        {k: card[k] for k in ("time", "key", "label", "seg")} | {"file": relpath}
        for card in vaultfmt.parse_cards(text, relpath)
        if card["valid"]
    ]


def cards_block(cards: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    cards = sorted(cards, key=lambda c: c["time"], reverse=True)
    unique, seen = [], set()
    for c in cards:
        mark = (c["seg"], c["time"])
        if mark not in seen:
            seen.add(mark)
            unique.append(c)
    return {"cards": unique[:limit], "total": len(unique)}


def idea_title(body: str, fallback: str) -> str:
    lines = iter(body.split("\n"))
    for line in lines:
        if line.strip().casefold() == "## idea":
            return next((x.strip() for x in lines if x.strip()), fallback)
    return fallback
