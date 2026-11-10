"""Near-duplicate detection and merging for pitfalls.md lines.

A pitfall line is `- text · triggers: a, b · evidence; segment 1234abcd · 2026-01-01`.
Two lines describe the same failure when their texts share most of their words, or when
their evidence quotes the same error message.
"""

import difflib
import re

from juno.core import fold, topics, writegate

WORD_RE = re.compile(r"[\w.=-]+")
MIN_WORD = 4
MIN_SHARED = 4
OVERLAP = 0.6
ERROR_MIN_CHARS = 20
ERROR_MIN_WORDS = 3


def words(text: str, preset: str = "none") -> set[str]:
    return {w for w in WORD_RE.findall(fold.fold(text, preset)) if len(w) >= MIN_WORD}


def near_text(a: str, b: str, preset: str = "none") -> bool:
    """Same wording (writegate's ratio) or most of the shorter text's words shared."""
    fa, fb = fold.fold(a, preset), fold.fold(b, preset)
    if fa == fb:
        return True
    if difflib.SequenceMatcher(None, fa, fb, autojunk=False).ratio() >= writegate.NEAR_RATIO:
        return True
    wa, wb = words(a, preset), words(b, preset)
    shared = len(wa & wb)
    return shared >= MIN_SHARED and shared / min(len(wa), len(wb)) >= OVERLAP


def error_pieces(evidence: str, preset: str = "none") -> list[str]:
    pieces = [fold.fold(p, preset) for p in evidence.split("; ")]
    return [p for p in pieces if len(p) >= ERROR_MIN_CHARS and len(p.split()) >= ERROR_MIN_WORDS]


def same_error(a: str, b: str, preset: str = "none") -> bool:
    pa, pb = error_pieces(a, preset), error_pieces(b, preset)
    return any(x in y or y in x for x in pa for y in pb)


def split(line: str) -> tuple[str, list[str], str] | None:
    """(text, triggers, evidence) of a pitfall line, or None when it is malformed."""
    found = topics.pitfall_line(line)
    parts = line[2:].rstrip().split(" · ")
    if found is None:
        return None
    return found["text"], found["triggers"], parts[2].rsplit("; segment ", 1)[0]


def matches(line: str, text: str, evidence: str, preset: str = "none") -> bool:
    have = split(line) if line.startswith("- ") else None
    if have is None:
        return False
    return near_text(have[0], text, preset) or same_error(have[2], evidence, preset)


def find(lines: list[str], text: str, evidence: str, preset: str = "none") -> int | None:
    for index, row in enumerate(lines):
        if matches(row, text, evidence, preset):
            return index
    return None


def merge(
    old: str,
    text: str,
    triggers: list[str],
    evidence: str,
    sid8: str,
    today: str,
    preset: str = "none",
) -> str:
    """Update `old` with a new sighting: longer text (and its evidence) wins, triggers unite."""
    have = split(old)
    if have is None:
        return old
    old_text, old_triggers, old_evidence = have
    keep_old = len(old_text.rstrip(".")) >= len(text.rstrip("."))
    chosen_text, chosen_ev = (old_text, old_evidence) if keep_old else (text, evidence)
    seen: dict[str, str] = {}
    for trigger in [*old_triggers, *triggers]:
        seen.setdefault(fold.fold(trigger, preset), trigger)
    shown = ", ".join(list(seen.values())[: topics.MAX_TRIGGERS]) or "-"
    row = f"- {chosen_text} · triggers: {shown} · {chosen_ev}; segment {sid8} · {today}"
    return row if len(row) <= topics.PITFALL_LINE_CHARS else old


def pairs(text: str, preset: str = "none") -> list[tuple[int, int]]:
    """1-based file line numbers of every near-duplicate pair among the pitfall lines."""
    rows = [
        (n, found)
        for n, row in enumerate(text.splitlines(), 1)
        if row.startswith("- ") and (found := split(row)) is not None
    ]
    return [
        (a, b)
        for (a, x), (b, y) in ((p, q) for i, p in enumerate(rows) for q in rows[i + 1 :])
        if near_text(x[0], y[0], preset) or same_error(x[2], y[2], preset)
    ]
