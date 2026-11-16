import fnmatch
import re
from collections.abc import Sequence
from typing import Any

from juno.core import fold, toolrec

TEXT_KEYS = (
    "text",
    "body",
    "message",
    "comment",
    "commentBody",
    "comment_body",
    "content",
    "markdown_text",
    "reply",
    "note",
    "description",
)
BASH_PATTERN = re.compile(r"^Bash\((.*)\)$", re.DOTALL)
HEREDOC_RE = re.compile(
    r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)\n[ \t]*\2[ \t]*(?:\n|$|\)|\")", re.DOTALL
)
BODY_FLAGS = ("--body", "-b")
FILE_FLAGS = ("--body-file", "-F")
WINDOW = 5
UNCHANGED_RATIO = 0.5
MIN_CHARS = 2


def command_of(tool: object, tool_in: dict[str, Any]) -> str:
    command = tool_in.get("command")
    return command if tool == "Bash" and isinstance(command, str) else ""


def matches_outward(tool: object, tool_in: dict[str, Any], patterns: Sequence[str]) -> bool:
    name = str(tool or "").casefold()
    command = command_of(tool, tool_in).casefold()
    for pattern in patterns:
        bash = BASH_PATTERN.match(pattern)
        if bash:
            if command and fnmatch.fnmatchcase(command, bash.group(1).casefold()):
                return True
        elif fnmatch.fnmatchcase(name, pattern.casefold()):
            return True
    return False


def bash_body(command: str) -> str:
    heredoc = HEREDOC_RE.search(command)
    if heredoc and any(flag in command for flag in BODY_FLAGS):
        return heredoc.group(3)
    tokens = toolrec.shell_tokens(command)
    for i, token in enumerate(tokens):
        if token in BODY_FLAGS and i + 1 < len(tokens):
            return tokens[i + 1]
        if token.startswith("--body="):
            return token[len("--body=") :]
    return ""


def body_file(command: str) -> str:
    """The path after `--body-file` / `-F` of a gh command; empty for stdin (`-`) or none."""
    tokens = toolrec.shell_tokens(command)
    for i, token in enumerate(tokens):
        path = ""
        if token in FILE_FLAGS and i + 1 < len(tokens):
            path = tokens[i + 1]
        elif token.startswith("--body-file="):
            path = token[len("--body-file=") :]
        if path and path != "-":
            return path
    return ""


def default_register(tool: object, tool_in: dict[str, Any], formal_targets: Sequence[str]) -> str:
    """The register of an outward call when the request named none."""
    return "formal" if matches_outward(tool, tool_in, formal_targets) else "voice"


def _find(tokens: list[str], entry: list[str], prefix: bool) -> list[int]:
    n = len(entry)
    hits = []
    for i in range(len(tokens) - n + 1):
        part = tokens[i : i + n]
        if all(
            a == b or (prefix and len(b) > 3 and a.startswith(b))
            for a, b in zip(part, entry, strict=True)
        ):
            hits.append(i)
    return hits


def _words(text: str, preset: str) -> list[str]:
    return re.findall(r"\w+", fold.fold(text, preset).replace("\u2019", "'"))


def _entries(items: Sequence[str], preset: str) -> list[list[str]]:
    return [e for e in (_words(i, preset) for i in items) if e]


def is_standing(text: str, preset: str, standing: Sequence[str]) -> bool:
    """A standing preference ("always", "from now on", a third-person optative such as
    `hazirlasin`): it names no text to write now, so it is never a request."""
    tokens = _words(text, preset)
    return any(_find(tokens, e, False) for e in _entries(standing, preset))


def draft_request(
    text: str,
    preset: str,
    phrases: Sequence[str],
    verbs: Sequence[str],
    targets: Sequence[str],
    standing: Sequence[str],
) -> bool:
    """A write/send/post/draft verb aimed at a target within a few words, and no standing
    preference. Cue words alone (a register name) are not a request."""
    if is_standing(text, preset, standing):
        return False
    if fold.has_cue(text, phrases, preset):
        return True
    tokens = _words(text, preset)
    aims = [i for e in _entries(targets, preset) for i in _find(tokens, e, True)]
    for verb in _entries(verbs, preset):
        for i in _find(tokens, verb, False):
            if any(i - WINDOW <= j <= i + len(verb) + WINDOW for j in aims):
                return True
    return False


def post_text(tool: object, tool_in: dict[str, Any]) -> str:
    command = command_of(tool, tool_in)
    if command:
        return bash_body(command).strip()
    for key in TEXT_KEYS:
        value = tool_in.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def target_kind(tool: object, tool_in: dict[str, Any]) -> str:
    command = command_of(tool, tool_in).casefold()
    name = str(tool or "").casefold()
    if command:
        return "review" if "gh pr review" in command or "gh pr comment" in command else "code"
    if "mail" in name:
        return "mail"
    if "comment" in name:
        return "tracker"
    return "chat"


def norm(text: str) -> str:
    return " ".join(text.casefold().split())


def words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def same_text(posted: str, draft: str) -> bool:
    a, b = norm(posted), norm(draft)
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b:
        return len(a) >= UNCHANGED_RATIO * len(b)
    wa, wb = words(a), words(b)
    return bool(wa | wb) and len(wa & wb) / len(wa | wb) >= 0.9


def related(posted: str, draft: str) -> bool:
    mine = words(posted)
    return bool(mine) and len(mine & words(draft)) / len(mine) >= 0.5


def authorship(posted: str, draft: str, typed: Sequence[str]) -> str:
    """`user` typed it, `edited` is a changed draft after feedback, `claude` is as drafted."""
    flat = norm(posted)
    if len(flat) >= MIN_CHARS and any(flat in norm(t) for t in typed):
        return "user"
    if draft and same_text(posted, draft):
        return "claude"
    if draft and typed and related(posted, draft):
        return "edited"
    return "claude"


def clip(text: str, limit: int) -> str:
    data = text.encode("utf-8")
    return text if len(data) <= limit else data[:limit].decode("utf-8", "ignore")
