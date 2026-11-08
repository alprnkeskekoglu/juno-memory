import re
from collections import Counter
from dataclasses import dataclass

from juno.core.vaultfmt import CARD_HEAD_RE, parse_frontmatter

FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
FROM_RE = re.compile(r"^## From handoffs/")


@dataclass(frozen=True)
class Block:
    date: str
    time: str
    text: str


def lines_of(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def split_blocks(text: str) -> tuple[str, list[Block]]:
    preamble: list[str] = []
    heads: list[tuple[str, str]] = []
    parts: list[list[str]] = []
    in_fence = False
    for line in lines_of(text):
        if FENCE_RE.match(line):
            in_fence = not in_fence
        match = None if in_fence else CARD_HEAD_RE.match(line.rstrip("\r\n"))
        if match:
            stamp = match.group(1)
            heads.append((stamp[:10], stamp[11:]))
            parts.append([line])
        elif parts:
            parts[-1].append(line)
        else:
            preamble.append(line)
    blocks = [Block(d, t, "".join(p)) for (d, t), p in zip(heads, parts, strict=True)]
    return "".join(preamble), blocks


def block_key(text: str) -> str:
    lines = lines_of(text)
    while lines and (not lines[-1].strip() or FROM_RE.match(lines[-1])):
        lines.pop()
    return "".join(lines).rstrip()


def archive_counter(text: str) -> Counter[str]:
    return Counter(block_key(b.text) for b in split_blocks(text)[1])


def fence_open(text: str) -> bool:
    open_ = False
    for line in lines_of(text):
        if FENCE_RE.match(line):
            open_ = not open_
    return open_


def ensure_private(text: str, title: str) -> str:
    if not text.strip():
        return f"---\nvisibility: private\n---\n\n# {title}\n"
    lines = lines_of(text)
    if lines[0].rstrip() == "---":
        for i in range(1, len(lines)):
            if lines[i].rstrip() not in ("---", "..."):
                continue
            for j in range(1, i):
                if re.match(r"visibility[ \t]*:", lines[j]):
                    if parse_frontmatter(text)[0].get("visibility") == "private":
                        return text
                    newline = "\r\n" if lines[j].endswith("\r\n") else "\n"
                    lines[j] = "visibility: private" + newline
                    return "".join(lines)
            newline = "\r\n" if lines[i].endswith("\r\n") else "\n"
            lines.insert(i, "visibility: private" + newline)
            return "".join(lines)
    return "---\nvisibility: private\n---\n\n" + text


def append_section(text: str, source: str, blocks: list[str]) -> str:
    if text and not text.endswith("\n"):
        text += "\n"
    header = f"## From handoffs/{source}\n"
    last = None
    for line in lines_of(text):
        if FROM_RE.match(line):
            last = line
    if last != header:
        if text and not text.endswith("\n\n"):
            text += "\n"
        text += header + "\n"
    for block in blocks:
        text += block if block.endswith("\n") else block + "\n"
    return text


def fresh_blocks(archive: str, texts: list[str]) -> list[str]:
    if fence_open(archive):
        return [t for t in texts if t not in archive]
    have = archive_counter(archive)
    fresh: list[str] = []
    for text in texts:
        key = block_key(text)
        if have[key] > 0:
            have[key] -= 1
        else:
            fresh.append(text)
    return fresh


def archive_holds(archive: str, texts: list[str]) -> bool:
    have = archive_counter(archive)
    need = Counter(block_key(t) for t in texts)
    if all(have[k] >= n for k, n in need.items()):
        return True
    return all(t in archive for t in texts)


@dataclass(frozen=True)
class Plan:
    kept: str
    moved: dict[str, list[str]]
    count: int


def plan(preamble: str, blocks: list[Block], max_cards: int) -> Plan | None:
    if len(blocks) <= max_cards:
        return None
    order = sorted(range(len(blocks)), key=lambda i: (blocks[i].date, blocks[i].time, i))
    moved = set(order[: len(blocks) - max_cards])
    by_month: dict[str, list[str]] = {}
    for i in sorted(moved, key=lambda i: (blocks[i].date, blocks[i].time, i)):
        by_month.setdefault(blocks[i].date[:7], []).append(blocks[i].text)
    kept = preamble + "".join(b.text for i, b in enumerate(blocks) if i not in moved)
    return Plan(kept, by_month, len(moved))
