import re
from collections.abc import Collection
from dataclasses import dataclass

from juno.core import fold, taskfile, writegate
from juno.core.detect import Tickets

HEADING_RE = re.compile(r"^#{1,2}\s")


@dataclass(frozen=True)
class Owner:
    """Where a decision lives: `rel` is vault-relative, `kind` is profile, ticket or topic, and
    `topic` is the normalised name used for `archive/decisions/<topic>.md`."""

    rel: str
    kind: str
    topic: str


def decision_owner(topic: str, profiles: Collection[str], tickets: Tickets) -> Owner:
    """`profiles` holds the VISIBLE profile stems (projects/<stem>.md); a hidden one is absent."""
    if topic in profiles:
        return Owner(f"projects/{topic}.md", "profile", topic)
    key = topic.upper()
    if tickets.enabled and tickets.find(key) == [key]:
        return Owner(f"tasks/{key}.md", "ticket", key)
    name = taskfile.canonical_topic(topic)
    if not taskfile.TICKET_TOPIC_RE.match(name):
        name = writegate.check_slug(name, "topic")
    return Owner(f"decisions/{name}.md", "topic", name)


def legacy_decision_rels(kind: str, key_or_name: str) -> list[str]:
    """Where a not-yet-migrated decisions file for this owner may live, in lookup order."""
    if kind == "ticket":
        return [f"decisions/{taskfile.canonical_topic(key_or_name)}.md"]
    name = fold.safe_name(key_or_name)
    rels = [f"decisions/{name}.md", f"decisions/{taskfile.canonical_topic(name)}.md"]
    return list(dict.fromkeys(rels))


def merged_decisions(owner_section: str, *legacy_currents: str) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for text in (owner_section, *legacy_currents):
        for line in text.splitlines():
            if line.strip() and line not in seen:
                seen.add(line)
                merged.append(line)
    return merged


def heading_index(lines: list[str], name: str) -> int | None:
    want = re.compile(r"^##\s+" + re.escape(name) + r"\s*$")
    return next((i for i, x in enumerate(lines) if want.match(x)), None)


def ensure_section(text: str, name: str, first: bool = True) -> str:
    lines = text.split("\n")
    if heading_index(lines, name) is not None:
        return text
    start = 0
    fm_end = taskfile.frontmatter_end(lines)
    if fm_end is not None:
        start = fm_end + 1
    nxt = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), None)
    if first and nxt is not None:
        return "\n".join([*lines[:nxt], f"## {name}", "", *lines[nxt:]])
    body = text.rstrip("\n")
    return f"{body}\n\n## {name}\n" if body.strip() else f"## {name}\n"


def set_section_lines(text: str, name: str, lines: list[str]) -> str:
    text = ensure_section(text, name)
    parts = text.split("\n")
    head = heading_index(parts, name)
    assert head is not None
    end = next((i for i in range(head + 1, len(parts)) if HEADING_RE.match(parts[i])), len(parts))
    old = parts[head + 1 : end]
    blanks = 0
    while blanks < len(old) and old[len(old) - 1 - blanks] == "":
        blanks += 1
    body = ["", *lines] if lines else []
    tail = [""] * max(blanks, 1)
    return "\n".join([*parts[: head + 1], *body, *tail, *parts[end:]])


ARCHIVE_DIR = "archive/decisions"


def archive_rel(topic: str) -> str:
    return f"{ARCHIVE_DIR}/{topic}.md"


def archive_append(text: str, topic: str, lines: list[str]) -> str:
    """Append `lines` under `## History` of an archive file ("" when missing); idempotent."""
    if not text.strip():
        text = f"# Superseded decisions: {topic}\n\n## History\n"
    present = set(text.split("\n"))
    new = [x for x in dict.fromkeys(lines) if x not in present]
    parts = ensure_section(text, "History", first=False).split("\n")
    head = heading_index(parts, "History")
    assert head is not None
    end = next((i for i in range(head + 1, len(parts)) if HEADING_RE.match(parts[i])), len(parts))
    last = end
    while last > head + 1 and parts[last - 1] == "":
        last -= 1
    body = parts[head + 1 : last]
    if not body:
        new = ["", *new]
    rest = parts[last:] or [""]
    return "\n".join([*parts[:last], *new, *rest])


def superseded_line(old: str, day: str, owner_rel: str, write_id: str) -> str:
    return f"{old} · superseded {day} · from {owner_rel} · {write_id}"
