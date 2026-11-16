"""Trigger proposals for knowledge files (#36): from a file's title and body, and from the words
the user typed in the language they use when the file was read by hand. Pure; the session-end
run only queues them, the user confirms."""

import re
from collections import Counter
from collections.abc import Iterable

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
STOP = frozenset(
    """about after again also because been before being between could does doing down during each
    from have having here into just like made make many more most much must only other over same
    should since some such than that their them then there these they this those through under
    until very were what when where which while will with within without would your""".split()
)


def usable(word: str) -> bool:
    return (
        0 < len(word) <= MAX_WORD
        and not word.isdigit()
        and not word.startswith("http")
        and word not in STOP
        and word not in topics.GENERIC_TRIGGERS
        and topics.trigger_problem(word) is None
    )


def propose(title: str, body: str, preset: str = "none") -> list[str]:
    """Up to four triggers: terms that are specific by shape, in the title or said twice."""
    seen: Counter[str] = Counter()
    for weight, text in ((TITLE_WEIGHT, title), (1, CODE_RE.sub(" ", body))):
        for raw in WORD_RE.findall(text):
            word = fold.fold(raw.strip("./-_"), preset)
            if not usable(word):
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
