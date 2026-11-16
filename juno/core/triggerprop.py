"""Trigger proposals for knowledge files (#36): from a file's title and body, and from the words
the user typed in the language they use when the file was read by hand. Pure; the session-end
run only queues them, the user confirms."""

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from juno.core import fold, topics

MAX_PROPOSED = 4
MAX_USER_FORMS = 2
TITLE_WEIGHT = 3
SPECIFIC_WEIGHT = 2
MIN_PLAIN = 5
MIN_SPECIFIC = 4
MAX_WORD = 40
CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
WORD_RE = re.compile(r"\w[\w./-]*")
LETTERS_RE = re.compile(r"[^\W\d_]{5,}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?!\d)")
TICKET_RE = re.compile(r"[A-Z][A-Z0-9]*-\d+")
COMMON_SHARE = 4  # a token in more than 1/COMMON_SHARE of the knowledge files is not specific
COMMON_MIN_FILES = 4  # below this a quarter is under one file, so no token counts as common
STOP = frozenset(
    """about after again also because been before being between could does doing down during each
    from have having here into just like made make many more most much must only other over same
    should since some such than that their them then there these they this those through under
    until very were what when where which while will with within without would your""".split()
)


@dataclass(frozen=True)
class Vocab:
    """What the vault says about its own words: configured ticket prefixes and the tokens that
    occur in too many knowledge files. Built by the adapter; this module stays pure."""

    prefixes: tuple[str, ...] = ()
    common: frozenset[str] = frozenset()


def common_tokens(docs: Mapping[str, int], total: int) -> frozenset[str]:
    """Tokens (with their document frequency) found in more than a quarter of `total` files.
    With fewer than COMMON_MIN_FILES files nothing is common."""
    if total < COMMON_MIN_FILES:
        return frozenset()
    return frozenset(w for w, n in docs.items() if n * COMMON_SHARE > total)


def ticket_like(raw: str, prefixes: Sequence[str]) -> bool:
    if TICKET_RE.fullmatch(raw):
        return True
    return any(re.fullmatch(re.escape(p) + r"-\d+", raw, re.I) for p in prefixes if p)


def usable(word: str, vocab: Vocab = Vocab(), raw: str = "") -> bool:
    """`word` is the folded token, `raw` the token as written (ticket ids need their case)."""
    raw = raw or word
    return (
        0 < len(word) <= MAX_WORD
        and not word.isdigit()
        and not word.startswith("http")
        and not DATE_RE.match(raw)
        and not ticket_like(raw, vocab.prefixes)
        and word not in STOP
        and word not in topics.GENERIC_TRIGGERS
        and word not in vocab.common
        and topics.trigger_problem(word) is None
    )


def tokens(title: str, body: str, preset: str = "none", vocab: Vocab = Vocab()) -> set[str]:
    """Every usable token of a file, for the vault's document frequency."""
    found: set[str] = set()
    for text in (title, CODE_RE.sub(" ", body)):
        for raw in WORD_RE.findall(text):
            stripped = raw.strip("./-_")
            word = fold.fold(stripped, preset)
            if usable(word, vocab, stripped):
                found.add(word)
    return found


def propose(title: str, body: str, preset: str = "none", vocab: Vocab = Vocab()) -> list[str]:
    """Up to four triggers: terms that are specific by shape, in the title or said twice."""
    seen: Counter[str] = Counter()
    for weight, text in ((TITLE_WEIGHT, title), (1, CODE_RE.sub(" ", body))):
        for raw in WORD_RE.findall(text):
            stripped = raw.strip("./-_")
            word = fold.fold(stripped, preset)
            if not usable(word, vocab, stripped):
                continue
            specific = topics.is_specific(word)
            if len(word) < (MIN_SPECIFIC if specific else MIN_PLAIN):
                continue
            seen[word] += weight * (SPECIFIC_WEIGHT if specific else 1)
    kept = [w for w, score in seen.items() if score >= 2]
    kept.sort(key=lambda w: (-seen[w], -len(w), w))
    return kept[:MAX_PROPOSED]


def user_forms(
    said: str, item: str, existing: Iterable[str], preset: str, stopwords: Iterable[str]
) -> list[str]:
    """Words the user typed (longest first) that neither the item nor its triggers carry. Only
    with a language preset: without one the user's language is not known."""
    if preset == "none":
        return []
    skip = {fold.fold(w, preset) for w in stopwords} | set(existing) | topics.GENERIC_TRIGGERS
    skip |= set(re.findall(r"\w+", fold.fold(item, preset)))
    found: dict[str, None] = {}
    for word in LETTERS_RE.findall(fold.fold(said, preset)):
        if word not in skip and topics.trigger_problem(word) is None:
            found[word] = None
    return sorted(found, key=lambda w: -len(w))[:MAX_USER_FORMS]
