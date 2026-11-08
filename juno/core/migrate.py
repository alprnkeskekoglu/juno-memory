import re
from collections.abc import Callable, Mapping

CODE_SCHEMA_VERSION = 3

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
