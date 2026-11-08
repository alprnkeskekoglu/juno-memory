import re

FENCE_RE = re.compile(
    r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?(?:^[ \t]*\1[ \t]*$|\Z)", re.MULTILINE | re.DOTALL
)
PASTE_MARK_RE = re.compile(r"\[Pasted text[^\]]*\]|\[Image #\d+\]")
PASTED_CONTENT_RE = re.compile(r"<pasted_content\b[^>]*>.*?(?:</pasted_content>|\Z)", re.DOTALL)
LOG_LINE_RE = re.compile(
    r"^\s*(?:\[?\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}|\[?\d{2}:\d{2}:\d{2}[.,\]]|"
    r"(?:ERROR|WARN|WARNING|INFO|DEBUG|TRACE|FATAL)\b[:\] ]|Traceback \(most recent|"
    r"File \".*\", line \d+|at [\w$.<>]+\(.*\)\s*$|\w+(?:Error|Exception): )"
)
UNTRUSTED_TAGS = ("agent-message", "task-notification", "cross-session-message", "teammate-message")
PASSIVE_TAGS = (
    "system-reminder",
    "local-command-stdout",
    "local-command-stderr",
    "local-command-caveat",
    "command-name",
    "command-message",
    "command-args",
    "bash-input",
    "bash-stdout",
    "bash-stderr",
    "user-prompt-submit-hook",
)
BLOCK_OPEN_RE = re.compile(r"<(" + "|".join(UNTRUSTED_TAGS + PASSIVE_TAGS) + r")(?=[\s>/])[^>]*>")
LINE_MARKERS = (
    "another claude session sent a message:",
    "[subagent hand-back]",
    "[system notification",
)
USER_WRAPPER_RE = re.compile(
    r"\s*the user sent a new message while you were working\s*:[ \t]*", re.I
)
WRAPPER_TAIL_RE = re.compile(r"^[ \t]*important:", re.I | re.MULTILINE)
COMMAND_RE = re.compile(r"/[A-Za-z][\w.:-]*(?=[ \t]|$)")
NOSAVE = "[nosave]"


def strip_pastes(prompt: str | None) -> str:
    text = FENCE_RE.sub("", PASTED_CONTENT_RE.sub("", prompt or ""))
    text = PASTE_MARK_RE.sub("", text)
    return "\n".join(line for line in text.splitlines() if not LOG_LINE_RE.match(line)).strip()


def user_text(prompt: object) -> str:
    text = prompt if isinstance(prompt, str) else ""
    head = text.lstrip()
    if head.casefold().startswith(LINE_MARKERS) or BLOCK_OPEN_RE.match(head):
        return ""
    wrapper = USER_WRAPPER_RE.match(text)
    if not wrapper and not BLOCK_OPEN_RE.search(text):
        return text
    if wrapper:
        text = text[wrapper.end() :]
        tail = WRAPPER_TAIL_RE.search(text)
        text = text[: tail.start()] if tail else text
        if BLOCK_OPEN_RE.match(text.lstrip()):
            return ""
    out: list[str] = []
    pos = 0
    while True:
        found = BLOCK_OPEN_RE.search(text, pos)
        if not found:
            out.append(text[pos:])
            break
        out.append(text[pos : found.start()])
        close = text.find(f"</{found.group(1)}>", found.end())
        if close < 0 or found.group(1) in UNTRUSTED_TAGS:
            break
        pos = close + len(found.group(1)) + 3
    return "".join(out).strip()


def is_synthetic(prompt: object) -> bool:
    return isinstance(prompt, str) and bool(prompt.strip()) and not user_text(prompt)


def has_nosave(typed: str) -> bool:
    return NOSAVE in typed.casefold()
