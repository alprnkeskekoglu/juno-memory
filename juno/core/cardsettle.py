"""Pure text helpers to settle stale stub cards and duplicate cards of one segment."""

import re
from dataclasses import dataclass

from juno.core import cardfmt, vaultfmt
from juno.core.errors import Refused

SEG8_RE = re.compile(r"^[0-9a-f]{8}$")
FIELD_RE = re.compile(r"^(auto|Repos|Tickets|Learned|Links):", re.I)


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    text: str
    key: str
    stamp: str
    stub: bool


def spans(text: str, seg8: str) -> list[Span]:
    heads = list(re.finditer(r"^#{1,2} .*$", text, re.M))
    found: list[Span] = []
    for i, head in enumerate(heads):
        match = vaultfmt.CARD_HEAD_RE.match(head.group(0))
        if not match or match.group(4) != seg8:
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        block = text[head.start() : end]
        stub = re.search(r"^auto:\s*true\s*$", block, re.M) is not None
        found.append(Span(head.start(), end, block, match.group(2), match.group(1), stub))
    return found


def cut(text: str, drop: list[Span]) -> str:
    """The text without the given spans, with one blank line left between neighbours."""
    for span in sorted(drop, key=lambda s: s.start, reverse=True):
        before = text[: span.start].rstrip("\n") + "\n"
        after = text[span.end :].lstrip("\n")
        text = before + ("\n" + after if after else "")
    return text


def _items(block: str, name: str) -> list[str]:
    return vaultfmt.items(vaultfmt.card_field(block, name))


def _union(blocks: list[str], name: str) -> str:
    seen: list[str] = []
    for block in blocks:
        seen += [x for x in _items(block, name) if x not in seen]
    return ", ".join(seen) or "-"


def merge(blocks: list[str], seg8: str, prefixes: list[str] | None = None) -> str:
    """One card from several of one segment: first heading, joined bodies and Learned lines."""
    first = blocks[0].splitlines()[0]
    head = vaultfmt.CARD_HEAD_RE.match(first)
    if head is None:
        raise Refused("the first card has no valid heading")
    body: list[str] = []
    links: list[str] = []
    learned: list[str] = []
    for block in blocks:
        for line in block.splitlines()[1:]:
            if not line.strip():
                continue
            if line.lower().startswith("links:"):
                links += [x for x in [line[6:].strip()] if x and x not in links]
            elif line.lower().startswith("learned:"):
                value = line[8:].strip()
                if value and value.lower() != "none" and value not in learned:
                    learned.append(value.rstrip("."))
            elif not FIELD_RE.match(line) and line not in body:
                body.append(line)
    out = [
        first.rstrip(),
        "Repos: " + _union(blocks, "Repos"),
        "Tickets: " + _union(blocks, "Tickets"),
        *body,
    ]
    if links:
        out.append("Links: " + ", ".join(links))
    out.append("Learned: " + ("; ".join(learned) or "none"))
    try:
        card = cardfmt.validate_card(
            "\n".join(out) + "\n", seg8, head.group(2), head.group(1), prefixes
        )
    except cardfmt.Failed as exc:
        raise Refused(f"the merged card is invalid ({exc}); shorten the two cards") from None
    return card
