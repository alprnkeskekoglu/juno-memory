import re
from dataclasses import dataclass

from juno.core import compact, vaultfmt

START = "<!-- juno:tickets:start -->"
END = "<!-- juno:tickets:end -->"
NONE_TEXT = "_No tickets._"
START_RE = re.compile(r"<!--\s*juno:tickets(?::start)?\s*-->")
END_RE = re.compile(r"<!--\s*juno:tickets:end\s*-->")
HEADING_RE = re.compile(r"^##[ \t]+Tickets\b.*$")
GENERATED_RE = re.compile(r"^- \[\[tasks/[^\]]+\]\] · .*$")

Span = tuple[int, int]


@dataclass(frozen=True)
class Rewrite:
    text: str | None
    skipped: str
    manual: str | None


def inert(text: str) -> str:
    one = re.sub(r"[\s\x00-\x1f\x7f]+", " ", text).strip().replace("<", "&lt;").replace(">", "&gt;")
    return (
        one.replace("[[", "&#91;&#91;")
        .replace("]]", "&#93;&#93;")
        .replace("·", "&middot;")
        .replace("`", "&#96;")
    )


def ticket_line(key: str, status: str, title: str) -> str:
    return f"- [[tasks/{key}]] · {inert(status) or 'unknown'} · {inert(title) or key}"


def tickets_body(entries: list[str]) -> str:
    return "\n".join(entries) if entries else NONE_TEXT


def is_generated(body: str) -> bool:
    return all(
        not row.strip() or row.strip() == NONE_TEXT or GENERATED_RE.match(row.strip())
        for row in body.split("\n")
    )


def markers(text: str) -> tuple[list[Span], list[Span]]:
    starts: list[Span] = []
    ends: list[Span] = []
    in_fence = False
    pos = 0
    for line in text.splitlines(keepends=True):
        if compact.FENCE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            starts += [(pos + m.start(), pos + m.end()) for m in START_RE.finditer(line)]
            ends += [(pos + m.start(), pos + m.end()) for m in END_RE.finditer(line)]
        pos += len(line)
    return starts, ends


def rewrite(text: str, body: str) -> Rewrite:
    starts, ends = markers(text)
    if len(starts) == 1 and len(ends) == 1:
        (a0, a1), (b0, b1) = starts[0], ends[0]
        if b0 < a1:
            return Rewrite(None, "ambiguous tickets markers", None)
        old = text[a1:b0]
        manual = None if is_generated(old) else old
        return Rewrite(text[:a0] + START + "\n" + body + "\n" + END + text[b1:], "", manual)
    if starts or ends:
        return Rewrite(None, "ambiguous tickets markers", None)
    pieces = text.splitlines(keepends=True)
    in_fence = False
    for i, line in enumerate(pieces):
        if compact.FENCE_RE.match(line):
            in_fence = not in_fence
        if not in_fence and HEADING_RE.match(line.rstrip("\r\n")):
            head = line if line.endswith("\n") else line + "\n"
            block = f"{START}\n{body}\n{END}\n"
            return Rewrite("".join(pieces[:i]) + head + block + "".join(pieces[i + 1 :]), "", None)
    return Rewrite(None, "no Tickets heading or markers", None)


def entries_by_repo(tasks: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    by_repo: dict[str, list[tuple[str, str]]] = {}
    for key, text in tasks:
        fields, _ = vaultfmt.parse_frontmatter(text)
        status, title = fields.get("status"), fields.get("title")
        line = ticket_line(
            key, status if isinstance(status, str) else "", title if isinstance(title, str) else ""
        )
        for repo in vaultfmt.field_items(fields, "repos"):
            by_repo.setdefault(repo.casefold(), []).append((key, line))
    return by_repo


def profile_names(stem: str, text: str) -> set[str]:
    fields, _ = vaultfmt.parse_frontmatter(text)
    return {stem.casefold(), *(r.casefold() for r in vaultfmt.field_items(fields, "repo"))}
