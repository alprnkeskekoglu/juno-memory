import re

from juno.core import taskfile, vaultfmt
from juno.core.errors import Refused

SECTION_RE = re.compile(r"^[A-Z][A-Za-z0-9 -]{1,29}$")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
RESERVED = frozenset({"notes", "decisions", "links", "current", "history"})
FIELD = "digests"
HEADING_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$")
ALLOWED = "user words typed in this segment, or ticket:{key} for the ticket being written"


def valid_section(name: str) -> bool:
    return (
        bool(SECTION_RE.fullmatch(name))
        and name == " ".join(name.split())
        and name.casefold() not in RESERVED
    )


def check_section(name: str) -> str:
    if not valid_section(name):
        raise Refused(
            f"section {name!r} must start with a capital letter, hold 2-30 letters, digits, "
            "single spaces or hyphens, and not be Notes, Decisions, Links, Current or History"
        )
    return name


def check_key(key: str) -> str:
    if not KEY_RE.fullmatch(key):
        raise Refused(
            f"key {key!r} must be 1-40 lowercase letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )
    return key


def check_text(text: str) -> None:
    if "\n" in text or "\r" in text:
        raise Refused("line must be one line")
    if text.lstrip().startswith(("#", "[")):
        raise Refused("line must not start with '#' or '['")


def listed(doc: str) -> list[str]:
    return vaultfmt.field_items(vaultfmt.parse_frontmatter(doc)[0], FIELD)


def blocks(doc: str, doc_title: str) -> list[tuple[str, str]]:
    """Digest blocks of a ticket file for its pack, as (title, body), in `digests:` order."""
    found: list[tuple[str, str]] = []
    for name in dict.fromkeys(listed(doc)):
        body = vaultfmt.section(doc, name) if valid_section(name) else ""
        if body:
            found.append((f"{name} · {doc_title}", body))
    return found


def item_line(key: str, text: str, day: str) -> str:
    return f"- [{key}] {text} · {day}"


def put_line(body: str, digests: list[str], section: str, key: str, line: str) -> tuple[str, str]:
    """Put `line` under `## section` of a task file body; returns (new body, 'added'|'replaced').
    Raises Refused for a heading that is not a registered digest section."""
    lines = body.split("\n")
    heads = [(i, m.group(1)) for i, text in enumerate(lines) if (m := HEADING_RE.match(text))]
    same = [(i, name) for i, name in heads if name.casefold() == section.casefold()]
    if same and not (len(same) == 1 and same[0][1] == section and section in digests):
        raise Refused(
            f"tasks file already has a '## {same[0][1]}' section that is not a digest section "
            f"(not in {FIELD}:); Juno will not adopt it, pick another section name"
        )
    if not same:
        base = body if body.endswith("\n") else body + "\n"
        gap = "" if base.endswith("\n\n") or not base.strip() else "\n"
        return f"{base}{gap}## {section}\n{line}\n", "added"
    start = same[0][0] + 1
    stop = next(
        (i for i in range(start, len(lines)) if re.match(r"#{1,2}\s", lines[i])), len(lines)
    )
    prefix = f"- [{key}] "
    for i in range(start, stop):
        if lines[i].startswith(prefix):
            lines[i] = line
            return "\n".join(lines), "replaced"
    last = stop
    while last > start and not lines[last - 1].strip():
        last -= 1
    lines.insert(last, line)
    return "\n".join(lines), "added"


def set_digests(text: str, names: list[str]) -> str:
    """Write `digests: [..]` into the frontmatter; every other line stays as it was."""
    lines = text.split("\n")
    end = taskfile.frontmatter_end(lines)
    if end is None:
        return text
    new = taskfile.field_line(FIELD, names)
    for i in range(1, end):
        match = vaultfmt.FM_KEY_RE.match(lines[i])
        if match and match.group(1).lower() == FIELD and not lines[i][:1].isspace():
            stop = i + 1
            while stop < end and (lines[stop][:1] in (" ", "\t") or lines[stop].startswith("- ")):
                stop += 1
            return "\n".join([*lines[:i], new, *lines[stop:]])
    return "\n".join([*lines[:end], new, *lines[end:]])
