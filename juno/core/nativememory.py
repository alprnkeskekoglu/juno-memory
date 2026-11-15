"""Claude Code's native auto-memory files: parsing and mapping (pure, no I/O)."""

import re
from dataclasses import dataclass

TYPES = ("user", "feedback", "project", "reference")
BATCH = "memory-import"
INDEX_NAME = "MEMORY.md"
TEXT_MAX = 280
BATCH_MAX = 200
SELF_FILE = "communication"
SOURCE_RE = re.compile(r"^import:memory/[^/\s]{1,200}/[^/\s]{1,120}\.md$")
FM_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:[ \t]*(.*)$")
MARKUP_RE = re.compile(r"[*_`#>]+")


@dataclass(frozen=True)
class Note:
    file: str
    name: str
    description: str
    type: str
    body: str


def project_slug(cwd: str) -> str:
    return re.sub(r"[/.]", "-", cwd)


def match_repo(slug: str, repos: dict[str, str]) -> str | None:
    """Longest repo whose own slug equals the project slug or prefixes it (worktrees)."""
    best: tuple[int, str] | None = None
    for repo_slug, name in repos.items():
        if (slug == repo_slug or slug.startswith(repo_slug + "-")) and (
            best is None or len(repo_slug) > best[0]
        ):
            best = (len(repo_slug), name)
    return best[1] if best else None


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse(file: str, text: str) -> Note | None:
    """A memory file, or None when it is the index, has no frontmatter or an unknown type."""
    if file == INDEX_NAME or not file.endswith(".md"):
        return None
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if end is None:
        return None
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        match = FM_LINE_RE.match(line)
        if match:
            fields[match[1].casefold()] = unquote(match[2])
    kind = fields.get("type", "").casefold()
    if kind not in TYPES:
        return None
    name = fields.get("name") or file[:-3]
    return Note(file, name, fields.get("description", ""), kind, "\n".join(lines[end + 1 :]))


def clip(text: str, limit: int = TEXT_MAX) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0] or text[: limit - 1]
    return cut.rstrip(" ,;:.") + "…"


def first_line(body: str) -> str:
    for line in body.splitlines():
        plain = " ".join(MARKUP_RE.sub(" ", line).split())
        if plain:
            return plain
    return ""


def fact(note: Note, limit: int = TEXT_MAX) -> str:
    """One line for the vault: the description, else the first body line, else the name."""
    text = " ".join(note.description.split()) or first_line(note.body)
    if len(text) < 8:
        text = f"{note.name.replace('-', ' ').replace('_', ' ')}: {text}".strip(": ")
    return clip(text.replace("·", "-"), limit)


def topic(note: Note) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", note.name.casefold()).strip("-")[:59].strip("-")
    return slug if len(slug) >= 2 else "memory-" + (slug or "note")


def source(slug: str, file: str) -> str:
    return f"import:memory/{slug}/{file}"


def target(note: Note, repo: str | None) -> str:
    """Where a note lands: profile (gotchas), knowledge, rule candidate or self candidate."""
    if note.type == "feedback":
        return "rule"
    if note.type == "user":
        return "user"
    return "profile" if repo else "knowledge"


def self_file(note: Note) -> str:
    """The self/ file a user note is proposed for.

    self/ holds how to work with the person. Of its four files, `communication` is the only one
    for role, background and preferences; `values` and `tensions` would turn a note into a
    stance or a conflict and `decisions` needs a choice with a reason. So every user note goes
    to `communication` (the user can still `juno fix` a line afterwards).
    """
    return SELF_FILE


def rule_scope(repo: str | None) -> str:
    return f"repo:{repo}" if repo else "global"
