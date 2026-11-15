import re
from collections.abc import Callable, Mapping

from juno.core import vaultfmt, writegate

CODE_SCHEMA_VERSION = 6

Step = Callable[[str], None]
SCHEMA_RE = re.compile(r"^schema_version\s*=\s*(\d+)\s*$", re.M)

PRIVACY_BLOCK_RE = re.compile(
    r"(<!-- juno:invariant -->\n## Privacy\n)(.*?)(\n<!-- /juno:invariant -->)", re.S
)
PRIVACY_OLD = "Write memory only in this interactive session with me, never from a subagent."
PRIVACY_MARK = "stale fixer"
PRIVACY_NEW = (
    PRIVACY_OLD
    + " One exception, only while I keep `[stale] auto_fix` on: the weekly stale fixer may fix"
    " or archive a knowledge or profile line that the repo's default branch contradicts, citing"
    " `repo:<name>@<sha>:<path>:<line>`; each change is undoable with `juno undo`."
)

RULES_QUOTE_OLD = (
    "- The quote: at least 20 characters I typed this session, or of an "
    "AskUserQuestion answer I chose in this session (the chosen option's label or "
    "description, or my Other text or notes; not the question or other options), "
    "sharing a meaningful word (4+ letters) with the rule. Never invent or pad it."
)
RULES_QUOTE_NEW = (
    "- The quote: at least 20 characters I typed this session, or the label of an "
    "AskUserQuestion option I chose, or my Other text or notes there (never an "
    "option description or preview, the question or other options, so put the rule's "
    "key words in the option label), sharing a meaningful word (4+ letters) with the "
    "rule. Never invent or pad it."
)
CAPS_OLD = "Stay under the per-file and per-session caps."
CAPS_NEW = "Stay under the per-session cap."
LINKS_FORMAT_OLD = "Links: [[<repo>]], [[<KEY>]], ..."
LINKS_FORMAT_NEW = "Links: [[<repo>]] (why), ..."
LINKS_CMD_OLD = '[--links "[[<repo>]]"]'
LINKS_CMD_NEW = '[--links "[[<repo>]] (why)"]'


def read_schema(toml_text: str) -> int | None:
    match = SCHEMA_RE.search(toml_text)
    return int(match.group(1)) if match else None


def write_schema(toml_text: str, version: int) -> str:
    if SCHEMA_RE.search(toml_text):
        return SCHEMA_RE.sub(f"schema_version = {version}", toml_text, count=1)
    return f"schema_version = {version}\n" + toml_text


def plan(current: int, target: int, steps: Mapping[int, Step]) -> list[int]:
    wanted = list(range(current, target))
    missing = [v for v in wanted if v not in steps]
    if missing:
        raise ValueError(f"no migration step from schema {missing[0]} to {missing[0] + 1}")
    return wanted


def privacy_has_exception(core_text: str) -> bool:
    match = PRIVACY_BLOCK_RE.search(core_text)
    return match is not None and PRIVACY_MARK in match.group(2)


def add_stale_exception(core_text: str) -> str:
    match = PRIVACY_BLOCK_RE.search(core_text)
    if not match or PRIVACY_MARK in match.group(2) or PRIVACY_OLD not in match.group(2):
        return core_text
    body = match.group(2).replace(PRIVACY_OLD, PRIVACY_NEW, 1)
    return (
        core_text[: match.start()]
        + match.group(1)
        + body
        + match.group(3)
        + core_text[match.end() :]
    )


def replace_exact(text: str, old: str, new: str) -> str:
    return text.replace(old, new, 1)


def take_task_decisions(text: str) -> tuple[list[str], str]:
    lines = text.split("\n")
    start = next((i for i, x in enumerate(lines) if re.match(r"^##\s+Decisions\s*$", x)), None)
    if start is None:
        return [], text
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"^#{1,2}\s", lines[i])), len(lines)
    )
    found = [x.strip() for x in lines[start + 1 : end] if x.strip()]
    found = [x if x.startswith("- ") else "- " + x for x in found]
    rest = lines[:start] + lines[end:]
    while 0 < start < len(rest) and not rest[start - 1].strip() and not rest[start].strip():
        del rest[start]
    return found, "\n".join(rest)


def new_decision_file(topic: str, day: str) -> str:
    head = writegate.frontmatter({"topic": topic, "updated": day, "valid_from": day})
    return f"{head}\n## Current\n\n## History\n"


def add_decisions(text: str, lines: list[str], section: str) -> str:
    for line in lines:
        text, _ = writegate.add_item(text, line, "none", section=section)
    return text


def merge_decision_files(into: str, other: str) -> str:
    _, body = vaultfmt.parse_frontmatter(other)
    for section in ("Current", "History"):
        found = vaultfmt.section(body, section).split("\n")
        items = [x.strip() for x in found if x.startswith("- ")]
        into = add_decisions(into, items, section)
    return into
