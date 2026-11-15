import re
from dataclasses import dataclass, field
from typing import Any

from juno.core import inject, rank

TRIM_MARKER = "\n[... Juno truncated {} chars; see {} ...]"
CARD_KEEP_RE = re.compile(
    r"^(?:\*\*Decision:\*\*|Learned:|Repos:|Tickets:)|^\W*next\b|\bnext (?:steps?|up|session)\b",
    re.I,
)


@dataclass
class Ranking:
    """The text that decides what an over-budget list section keeps. `on` picks the ranked
    choice; otherwise the positional one stays and the ranked one is only recorded in `log`."""

    query: str
    on: bool
    log: list[dict[str, Any]] = field(default_factory=list)


def cut_words(line: str, room: int) -> str | None:
    if room < 20:
        return None
    head = line[: room - 2]
    return (head.rsplit(" ", 1)[0] if " " in head else head) + " …"


def card_lines(lines: list[str], room: int) -> list[str]:
    def key_line(i: int) -> bool:
        return i == 0 or bool(CARD_KEEP_RE.search(lines[i]))

    rank = sorted(range(len(lines)), key=lambda i: (not key_line(i), len(lines[i]) if i else 0, i))
    kept: dict[int, str] = {}
    used = -1
    for i in rank:
        line: str | None = lines[i]
        assert line is not None
        if used + 1 + len(line) > room:
            line = cut_words(line, room - used - 1) if key_line(i) else None
        if line:
            kept[i] = line
            used += 1 + len(line)
    return [kept[i] for i in sorted(kept)]


def list_items(lines: list[str]) -> list[str]:
    found: list[str] = []
    for line in lines:
        if found and line[:1] in (" ", "\t") and line.strip():
            found[-1] += "\n" + line
        else:
            found.append(line)
    return found


def keep_items(items: list[str], room: int) -> list[str]:
    # Lists grow by appending, so the newest items sit at the end: fill from
    # the end and return the kept items in their original display order.
    kept: list[str] = []
    used = -1
    for item in reversed(items):
        if used + 1 + len(item) > room:
            if not kept and not item.startswith("- "):
                cut = cut_words(item, room)
                kept = [cut] if cut else []
            break
        kept.append(item)
        used += 1 + len(item)
    return kept[::-1]


def trim_body(body: str, limit: int, source: str, ranking: Ranking | None = None) -> str:
    if len(body) <= limit:
        return body
    room = limit - len(TRIM_MARKER.format(len(body), source))
    lines = body.split("\n")
    if body.startswith("## "):
        kept = card_lines(lines, room)
    else:
        items = list_items(lines)
        kept = keep_items(items, room)
        picked = rank.ranked_indices(items, room, ranking.query) if ranking else None
        if ranking and picked:
            first = len(items) - len(kept)
            ranking.log.append(
                {
                    "source": source,
                    "items": len(items),
                    "positional": list(range(first, len(items))),
                    "ranked": picked,
                }
            )
            kept = [items[i] for i in picked] if ranking.on else kept
    text = "\n".join(kept)
    marker = TRIM_MARKER.format(len(body) - len(text), source)
    return text + marker if text else marker[1:]


def section_source(title: str) -> str:
    where = title.partition(" · ")[2].split()
    return where[0] if where else title


def share_room(sizes: list[int], room: int) -> dict[int, int]:
    shares: dict[int, int] = {}
    left = room
    for n, i in enumerate(sorted(range(len(sizes)), key=lambda i: (sizes[i], i))):
        shares[i] = min(sizes[i], left // (len(sizes) - n))
        left -= shares[i]
    return shares


def trim_pack(
    head: list[str],
    sections: list[tuple[str, str]],
    budget: int,
    full: str,
    ranking: Ranking | None = None,
    reserve_first: bool = False,
) -> str:
    """Trim sections to the budget. `reserve_first` is for P4 (task digests): when true the first
    section is satisfied in full before the others share the rest; ignored for now."""
    fixed = "\n".join(head)
    room = budget - len(fixed) - sum(len(title) + 4 for title, _ in sections)
    if room < 0:
        return inject.trim(full, budget, "the pack")
    shares = share_room([len(body) for _, body in sections], room)
    parts = [
        f"[{title}]\n{trim_body(body, shares[i], section_source(title), ranking)}"
        for i, (title, body) in enumerate(sections)
    ]
    text = "\n".join([fixed, *parts])
    return text if len(text) <= budget else inject.trim(full, budget, "the pack")
