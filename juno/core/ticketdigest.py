import re
from collections.abc import Sequence

from juno.core import fold

HEADER = "Ticket {key} (assistant messages that name it or follow a tool call on it, newest first):"
CALLS = ("tool", "bash")
SEP = "\n---\n"
HEADER_RE = re.compile(r"^Ticket (\S+) \(assistant messages that name it", re.M)
MIN_WORDS = 12
MIN_CHARS = 80
CUES = {
    "none": (
        "approach|plan|criteri(?:a|on)|accept\\w*|decid\\w*|decision|cause|fix\\w*|implement\\w*"
        "|test\\w*|passed|failed|blocked"
    ),
    "tr": (
        "yaklasim|plan|kriter|kabul|karar|kok neden|neden|duzelt|cozum|uygula|test|gecti"
        "|basarisiz|kaldi|bloke"
    ),
}


def mentions(text: str, key: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(key)}(?![\w-])", text, re.I) is not None


def cue_re(preset: str) -> re.Pattern[str]:
    """English cue words, joined with those of `preset` when it has some (matched on text with
    diacritics folded, so a word with or without its marks matches)."""
    words = [CUES["none"]] + ([CUES[preset]] if preset in CUES and preset != "none" else [])
    return re.compile(r"\b(?:" + "|".join(words) + r")", re.I)


def carries_content(text: str, key: str, preset: str = "none") -> bool:
    """True when an assistant message that names `key` says something about it: with the key
    removed it still has `MIN_WORDS` words and `MIN_CHARS` characters and holds one cue word of
    an approach, a criterion, a decision or a test result (`cue_re`, English plus the
    `[language] preset` words, read on the diacritic-folded text). A short note or a long
    reply with no such word only mentions the ticket."""
    if not mentions(text, key):
        return False
    rest = re.sub(rf"(?<![\w-]){re.escape(key)}(?![\w-])", " ", text, flags=re.I)
    folded = fold.ascii_fold(rest, preset)
    return (
        len(rest.split()) >= MIN_WORDS
        and len(" ".join(rest.split())) >= MIN_CHARS
        and cue_re(preset).search(folded) is not None
    )


def blocks_of(text: str) -> dict[str, str]:
    """The per-ticket blocks of a digest text, by key, as `block` wrote them."""
    marks = list(HEADER_RE.finditer(text))
    return {
        m.group(1): text[m.end() : marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        for i, m in enumerate(marks)
    }


def chat_worked(key: str, block_text: str, preset: str = "none") -> bool:
    """True when a ticket block holds a message that names `key` and carries content."""
    return any(carries_content(m, key, preset) for m in block_text.split(SEP))


def merge(
    calls: Sequence[tuple[int, str]], events: Sequence[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Events with each tool call put before the event that was next when it was made."""
    found: list[tuple[str, str]] = []
    todo = list(calls)
    for i, event in enumerate(events):
        while todo and todo[0][0] <= i:
            found.append(("tool", todo.pop(0)[1]))
        found.append(event)
    found += [("tool", text) for _, text in todo]
    return found


def about(key: str, events: Sequence[tuple[str, str]], i: int) -> bool:
    """True when the assistant message at `i` names `key`, or follows tool calls that did."""
    if mentions(events[i][1], key):
        return True
    for kind, text in reversed(events[:i]):
        if kind not in CALLS:
            return False
        if mentions(text, key):
            return True
    return False


def block_for(key: str, events: Sequence[tuple[str, str]], chars: int) -> str:
    head = HEADER.format(key=key)
    room = chars - len(head) - 1
    kept: list[str] = []
    used = 0
    for i in range(len(events) - 1, -1, -1):
        if events[i][0] != "assistant" or not about(key, events, i):
            continue
        text = events[i][1]
        if kept and used + len(text) + 5 > room:
            break
        kept.append(text if kept else text[: max(room, 0)])
        used += len(kept[-1]) + 5
    return head + "\n" + SEP.join(kept) if kept else ""


def block(keys: Sequence[str], events: Sequence[tuple[str, str]], chars: int) -> str:
    """The digest's per-ticket block: for each key the main-chain assistant messages that name it
    or follow a tool call on it, newest first, within `chars` shared by the keys. What does not
    fit is left out, so a line can only be written from what the digest shows."""
    if not keys or chars <= 0:
        return ""
    parts = [block_for(key, events, chars // len(keys)) for key in keys]
    return "\n\n".join(p for p in parts if p)
