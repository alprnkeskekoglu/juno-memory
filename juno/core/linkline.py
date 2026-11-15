"""The final `Links:` line of a note or knowledge file: `Links: [[target]] (reason), ...`."""

import re
from collections.abc import Callable, Sequence

from juno.core import links
from juno.core.errors import Refused

MIN_REASON_WORDS = 2
MIN_REASON_CHARS = 8
MAX_REASON_CHARS = 200
ENTRY_RE = re.compile(r"\[\[([^\[\]\n]+)\]\](?:[ \t]*\(([^()\n]*)\))?")
BAD_TARGET = ("[[", "]]", "|", "\n", " · ")
BAD_REASON = ("[[", "]]", "|", "\n", " · ", "(", ")")

Spec = tuple[str, str]
Tail = tuple[int, str]
Canon = Callable[[str], str | None]


def parse_spec(raw: str) -> Spec:
    target, sep, reason = raw.partition(":")
    target, reason = target.strip(), reason.strip()
    if not sep or not target:
        raise Refused(
            '--link takes "<target>:<reason>", e.g. --link "alpha:profile with the steps"'
        )
    if any(x in target for x in BAD_TARGET) or len(target) > links.MAX_TARGET:
        raise Refused(f"link target {target[:40]!r} contains ]], [[, |, ' · ' or a newline")
    if any(x in reason for x in BAD_REASON) or "·" in reason:
        raise Refused("link reason must not contain ]], [[, |, parentheses, '·' or a newline")
    return target, reason


def check_reason(reason: str) -> None:
    if (
        len(reason.split()) < MIN_REASON_WORDS
        or len(reason) < MIN_REASON_CHARS
        or len(reason) > MAX_REASON_CHARS
    ):
        raise Refused(
            f"link reason must be {MIN_REASON_WORDS}+ words and "
            f"{MIN_REASON_CHARS}-{MAX_REASON_CHARS} characters, saying why the link matters"
        )


def detach(text: str) -> tuple[str, Tail | None]:
    """The text without a final `Links:` line, and that line with the blank lines before it."""
    if not text.strip():
        return text, None
    lines = text.rstrip("\n").split("\n")
    if not lines[-1].startswith("Links:"):
        return text, None
    last = lines.pop()
    blanks = 0
    while lines and not lines[-1].strip():
        lines.pop()
        blanks += 1
    return "\n".join(lines) + "\n", (blanks, last)


def attach(text: str, tail: Tail) -> str:
    blanks, line = tail
    return text.rstrip("\n") + "\n" + "\n" * blanks + line + "\n"


def entries_of(line: str) -> list[str] | None:
    """The entries of a Links line, or None when the line is not made of `[[x]] (reason)`."""
    body = line[len("Links:") :].strip()
    if body in ("", "-"):
        return []
    found = list(ENTRY_RE.finditer(body))
    if not found or ", ".join(m.group(0) for m in found) != body:
        return None
    return [m.group(0) for m in found]


def with_links(text: str, specs: Sequence[Spec], canon: Canon) -> str:
    """The text with the links added to its final Links line (made if missing).

    A target that resolves to a file already linked gets the new reason in place."""

    def key(target: str) -> str:
        return canon(target) or "?" + target.casefold()

    body, tail = detach(text)
    entries = entries_of(tail[1]) if tail else []
    if entries is None:
        raise Refused(
            "the final Links line is not in the `[[target]] (reason)` form; fix it with `juno fix`"
        )
    wanted: dict[str, str] = {}
    for target, reason in specs:
        wanted[key(target)] = f"[[{target}]] ({reason})"
    for i, entry in enumerate(entries):
        match = ENTRY_RE.match(entry)
        replacement = wanted.pop(key(links.link_target(match.group(1))), None) if match else None
        if replacement:
            entries[i] = replacement
    entries += wanted.values()
    return attach(body, (tail[0] if tail else 1, "Links: " + ", ".join(entries)))
