"""Trigger proposals for knowledge files (#36): from a file's title and body, and from the words
the user typed in the language they use when the file was read by hand. Pure; the session-end
run only queues them, the user confirms."""

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

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
HEX_RE = re.compile(r"[0-9a-f]{7,40}")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
ORIGIN = "origin/"
PLAIN_MIN_COUNT = 2  # a plain word must occur this often in the file
PLAIN_MAX_OTHERS = 3  # and in fewer than this many other knowledge files
COMMON_SHARE = 4  # a token in more than 1/COMMON_SHARE of the knowledge files is not specific
COMMON_MIN_FILES = 4  # below this a quarter is under one file, so no token counts as common
STOP = frozenset(
    """about after again also because been before being between could does doing down during each
    from have having here into just like made make many more most much must only other over same
    should since some such than that their them then there these they this those through under
    until very were what when where which while will with within without would your""".split()
)
PROSE = frozenset(
    """existing default found command reads users index links force partner use used using uses
    new old file files line lines value values run runs set sets get gets add adds check show
    shows make makes need needs when then also only other same first last each every data list
    name names path paths type types text case cases rule rules item items change changes work
    works read write writes user test tests call calls note notes keep real part step steps true
    false first second next previous current later early many some any all both either few
    several another result results error errors output input option options mode state key keys
    code way ways thing things time times day days one two three based set unset without via
    like still already often usually never always sure able likely enough since because""".split()
)
PROSE_TR = frozenset(
    """ve ile icin bir bu su olan gibi daha cok yok var degil sonra once kadar ama veya her
    hem ise ancak sadece zaten bile""".split()
)


def prose(word: str, preset: str) -> bool:
    """Common prose words make poor triggers (proposal only; the matcher does not use this)."""
    return word in PROSE or (preset == "tr" and word in PROSE_TR)


@dataclass(frozen=True)
class Vocab:
    """What the vault says about its own words: configured ticket prefixes and the tokens that
    occur in too many knowledge files. Built by the adapter; this module stays pure."""

    prefixes: tuple[str, ...] = ()
    common: frozenset[str] = frozenset()
    folders: frozenset[str] = frozenset()  # vault top-level folders; `<folder>/...` is a path
    slugs: frozenset[str] = frozenset()  # stems of the vault's knowledge and other topic files
    docs: Mapping[str, int] = field(default_factory=dict)  # knowledge files holding each token

    def without(self, own: str) -> Vocab:
        """The vocabulary for the file whose slug is `own` (its own slug is a topic, not a link)."""
        return replace(self, slugs=self.slugs - {own}) if own in self.slugs else self


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


def hash_like(part: str) -> bool:
    """A uuid, or a git hash: 7-40 hex chars with a digit and a letter (so `added`, `face`,
    `1234567` are not hashes)."""
    low = part.lower()
    if UUID_RE.fullmatch(low):
        return True
    return bool(HEX_RE.fullmatch(low)) and not low.isdigit() and any(c.isdigit() for c in low)


def part_rejected(part: str, folded: str, vocab: Vocab) -> bool:
    return (
        bool(DATE_RE.match(part))
        or ticket_like(part, vocab.prefixes)
        or hash_like(part)
        or folded in vocab.common
        or folded.removesuffix(".md") in vocab.slugs
    )


def path_noise(word: str, raw: str, vocab: Vocab) -> bool:
    """A vault or remote path (`handoffs/x`, `origin/x`), or a token with a rejected part."""
    if word.startswith(ORIGIN) or (word.split("/", 1)[0] in vocab.folders and "/" in word):
        return True
    raws = raw.split("/")
    folds = word.split("/")
    if len(raws) != len(folds):
        raws = folds
    return any(part_rejected(r, f, vocab) for r, f in zip(raws, folds, strict=True) if f)


def usable(word: str, vocab: Vocab = Vocab(), raw: str = "") -> bool:
    """`word` is the folded token, `raw` the token as written (ticket ids need their case)."""
    raw = raw or word
    return (
        0 < len(word) <= MAX_WORD
        and not word.isdigit()
        and not word.startswith("http")
        and not path_noise(word, raw, vocab)
        and word not in STOP
        and word not in topics.GENERIC_TRIGGERS
        and topics.trigger_problem(word) is None
    )


def tokens(title: str, body: str, preset: str = "none", vocab: Vocab = Vocab()) -> set[str]:
    """Every usable token of a file, for the vault's document frequency."""
    found: set[str] = set()
    for text in (title, CODE_RE.sub(" ", body)):
        for raw in WORD_RE.findall(text):
            stripped = raw.strip("./-_")
            word = fold.fold(stripped, preset)
            if usable(word, vocab, stripped) and not prose(word, preset):
                found.add(word)
    return found


def plain_ok(word: str, count: int, vocab: Vocab) -> bool:
    if topics.is_specific(word):
        return True
    others = max(vocab.docs.get(word, 0) - 1, 0)  # the file itself is one of the documents
    return count >= PLAIN_MIN_COUNT and others < PLAIN_MAX_OTHERS


def propose(
    title: str, body: str, preset: str = "none", vocab: Vocab = Vocab(), own: str = ""
) -> list[str]:
    """Up to four triggers: terms that are specific by shape, in the title or said twice. A plain
    word must also occur twice in the file and in fewer than 3 other knowledge files. `own` is
    the file's slug, which stays proposable."""
    vocab = vocab.without(own)
    seen: Counter[str] = Counter()
    count: Counter[str] = Counter()
    for weight, text in ((TITLE_WEIGHT, title), (1, CODE_RE.sub(" ", body))):
        for raw in WORD_RE.findall(text):
            stripped = raw.strip("./-_")
            word = fold.fold(stripped, preset)
            if not usable(word, vocab, stripped) or prose(word, preset):
                continue
            specific = topics.is_specific(word)
            if len(word) < (MIN_SPECIFIC if specific else MIN_PLAIN):
                continue
            seen[word] += weight * (SPECIFIC_WEIGHT if specific else 1)
            count[word] += 1
    kept = [w for w, score in seen.items() if score >= 2 and plain_ok(w, count[w], vocab)]
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
