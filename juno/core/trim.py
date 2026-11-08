import re

from juno.core import inject

TRIM_MARKER = "\n[... Juno truncated {} chars; see {} ...]"
CARD_KEEP_RE = re.compile(
    r"^(?:\*\*Decision:\*\*|Learned:|Repos:|Tickets:)|^\W*next\b|\bnext (?:steps?|up|session)\b",
    re.I,
)


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
    kept: list[str] = []
    used = -1
    for item in items:
        if used + 1 + len(item) > room:
            if not kept and not item.startswith("- "):
                cut = cut_words(item, room)
                kept = [cut] if cut else []
            break
        kept.append(item)
        used += 1 + len(item)
    return kept


def trim_body(body: str, limit: int, source: str) -> str:
    if len(body) <= limit:
        return body
    room = limit - len(TRIM_MARKER.format(len(body), source))
    lines = body.split("\n")
    kept = (
        card_lines(lines, room) if body.startswith("## ") else keep_items(list_items(lines), room)
    )
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


def trim_pack(head: list[str], sections: list[tuple[str, str]], budget: int, full: str) -> str:
    fixed = "\n".join(head)
    room = budget - len(fixed) - sum(len(title) + 4 for title, _ in sections)
    if room < 0:
        return inject.trim(full, budget, "the pack")
    shares = share_room([len(body) for _, body in sections], room)
    parts = [
        f"[{title}]\n{trim_body(body, shares[i], section_source(title))}"
        for i, (title, body) in enumerate(sections)
    ]
    text = "\n".join([fixed, *parts])
    return text if len(text) <= budget else inject.trim(full, budget, "the pack")
