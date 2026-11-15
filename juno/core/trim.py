import re
from dataclasses import dataclass, field
from typing import Any

from juno.core import inject, rank

DIGEST_FLOOR = 600
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


def prioritized(sizes: list[int], room: int, priority: list[int]) -> dict[int, int]:
    """Shares for `trim_pack(priority=...)`: the first section keeps its full size unless that
    would leave less than `DIGEST_FLOOR` (or all the others, if smaller, or half the room) for
    them, then it is cut to `room - floor`. Of the remainder, a third goes first to the sections
    not in `priority` (sharing by size) unless it is at most the floor; the `priority` sections
    then take what is left in turn, each up to 3/4 of it, and the unlisted sections get the rest."""
    floor = min(DIGEST_FLOOR, sum(sizes[1:]), room // 2)
    first = 0 if 0 in priority else max(min(sizes[0], room - floor), 0)
    rest = [i for i in range(1, len(sizes)) if i not in priority]
    left = room - first
    held = share_room([sizes[i] for i in rest], left // 3 if left > DIGEST_FLOOR else 0)
    left -= sum(held.values())
    shares = {0: first}
    for n, i in enumerate(priority):
        shares[i] = min(sizes[i], left if n == len(priority) - 1 else -(-left * 3 // 4))
        left -= shares[i]
    final = share_room([sizes[i] for i in rest], left + sum(held.values()))
    shares.update({rest[n]: share for n, share in final.items()})
    return shares


def drop_cut(
    sections: list[tuple[str, str]], room: int, priority: list[int], ranking: Ranking | None
) -> list[str]:
    """Parts for `trim_pack(priority=...)`. A section whose share cannot hold its truncation
    marker is dropped, the least wanted first, and its room goes back to the others."""
    dropped: list[int] = []
    while True:
        live = [i for i in range(len(sections)) if i not in dropped]
        gain = sum(len(sections[i][0]) + 4 for i in dropped)
        got = prioritized(
            [len(sections[i][1]) for i in live],
            room + gain,
            [live.index(i) for i in priority if i in live],
        )
        shares = {live[n]: share for n, share in got.items()}
        bad = [
            i
            for i in live
            if i
            and shares[i] < len(sections[i][1])
            and shares[i]
            < len(TRIM_MARKER.format(len(sections[i][1]), section_source(sections[i][0])))
        ]
        if not bad:
            break
        dropped.append(max(bad, key=lambda i: priority.index(i) if i in priority else -1))
    return [
        f"[{title}]\n{trim_body(body, shares[i], section_source(title), ranking)}"
        for i, (title, body) in enumerate(sections)
        if i not in dropped
    ]


def trim_pack(
    head: list[str],
    sections: list[tuple[str, str]],
    budget: int,
    full: str,
    ranking: Ranking | None = None,
    reserve_first: bool = False,
    priority: list[int] | None = None,
) -> str:
    """Trim sections to the budget. With `reserve_first` the first section (Decisions) is kept in
    full before the others share the rest, if it takes at most half of the room. With `priority`
    (section indexes, keep-first order; needs `reserve_first`) the first section keeps its full
    size down to a floor left for the rest, then those sections fill in that order, the rest
    share what is left, and a section left without room for its truncation marker is dropped."""
    fixed = "\n".join(head)
    room = budget - len(fixed) - sum(len(title) + 4 for title, _ in sections)
    if room < 0:
        return inject.trim(full, budget, "the pack")
    sizes = [len(body) for _, body in sections]
    if reserve_first and priority and sizes:
        parts = drop_cut(sections, room, priority, ranking)
        text = "\n".join([fixed, *parts])
        return text if len(text) <= budget else inject.trim(full, budget, "the pack")
    if reserve_first and sizes and sizes[0] * 2 <= room:
        rest = share_room(sizes[1:], room - sizes[0])
        shares = {0: sizes[0], **{i + 1: share for i, share in rest.items()}}
    else:
        shares = share_room(sizes, room)
    parts = [
        f"[{title}]\n{trim_body(body, shares[i], section_source(title), ranking)}"
        for i, (title, body) in enumerate(sections)
    ]
    text = "\n".join([fixed, *parts])
    return text if len(text) <= budget else inject.trim(full, budget, "the pack")
