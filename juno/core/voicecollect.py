import fnmatch
import re
from collections.abc import Sequence
from typing import Any

from juno.core import toolrec

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
)
BASH_PATTERN = re.compile(r"^Bash\((.*)\)$", re.DOTALL)
HEREDOC_RE = re.compile(
    r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)\n[ \t]*\2[ \t]*(?:\n|$|\)|\")", re.DOTALL
)
BODY_FLAGS = ("--body", "-b")
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
