import re
import unicodedata
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from juno.core import privacy

REGISTERS = ("voice", "formal")
COUNTED = ("user", "edited", "import")
EXAMPLE_BY = ("user", "import")
MAX_EXAMPLES = 8
EXAMPLE_CHARS = 600
CORRECTION_LINES = 5
DRAFT_EXCERPT = 80

BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S", re.MULTILINE)
BOLD_RE = re.compile(r"\*\*[^*\n]+\*\*|__[^_\n]+__")
HEADER_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE)
LABEL_RE = re.compile(r"^\s*\**[A-Z][\w /-]{1,24}:\**(?:\s|$)", re.MULTILINE)
CODE_RE = re.compile(r"`[^`\n]+`|```")
LINK_RE = re.compile(r"https?://\S+|\[[^\]\n]+\]\([^)\n]+\)")
EMOJI_RE = re.compile("[\U0001f300-\U0001faff☀-⛿✀-➿]")
SEPARATOR_RE = re.compile("[—→⇒·]")
DASH_RE = re.compile(r"[–—]|\s-\s")
PAREN_RE = re.compile(r"\([^)\n]*\)")
OFFER_RE = re.compile(r"(?i)let me know|feel free|happy to help|hope this helps|questions\?")
GREETING_RE = re.compile(r"(?i)^\W*(?:hi|hello|dear|good (?:morning|afternoon|evening))\b")
SIGNOFF_RE = re.compile(
    r"(?i)\b(?:best regards|kind regards|regards|cheers|sincerely|best wishes)\W*$"
)
EVIDENCE_RE = re.compile(r"(?i)\((?:proven|verified|confirmed|likely|unverified)\)|most likely")
PATH_RE = re.compile(r"(?:[\w.-]+/)+[\w.-]+\.\w+|\b[\w-]+\.(?:py|ts|js|md|json|toml|ya?ml|go|rs)\b")
WORD_RE = re.compile(r"[^\W_]+")

BASE_LIST = (
    'greeting formulas and sign-offs; closing offers ("let me know if..."); bold Label: lines '
    "and headers in a message; bullets for two or three points; em dashes, arrows and "
    "middle-dot separators; emoji the user does not use; restating context the reader already "
    'has; evidence labels ("proven", "most likely"); file paths and identifiers the reader '
    "does not need; politeness formulas the user does not use"
)
TWO_VOICES = (
    "Two voices never mix: Juno's own character, humour and phrasing belong only to replies to "
    "the user. Text sent under the user's name uses this register and carries no trace of "
    "Juno's character; a skill with its own document format (ticket description, PR template) "
    "keeps that format, still without the AI tells."
)
REGISTER_TEXT = {
    "voice": "the user's own everyday style",
    "formal": "the user's own careful style: complete sentences, still not a model's formality",
}


@dataclass(frozen=True)
class Measures:
    n: int
    med_chars: int
    p90_chars: int
    med_words: int
    p90_words: int
    multiline: float
    lower_start: float
    period_end: float
    non_ascii: float
    bullets: float
    bold: float
    headers: float
    code: float
    links: float
    emoji: float
    semicolons: float
    parens: float
    dashes: float
    colons: float
    openers: tuple[str, ...]
    closers: tuple[str, ...]


@dataclass(frozen=True)
class Profile:
    reg: str
    n: int
    ready: bool
    measures: Measures | None
    examples: tuple[str, ...]


def percentile(values: Sequence[int], q: float) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    return ordered[min(len(ordered) - 1, max(0, round(q * len(ordered) + 0.5) - 1))]


def share(texts: Sequence[str], test: Any) -> float:
    return sum(1 for t in texts if test(t)) / len(texts) if texts else 0.0


def non_ascii_letters(text: str) -> bool:
    return any(ord(c) > 127 and unicodedata.category(c).startswith("L") for c in text)


def common_words(found: Sequence[str]) -> tuple[str, ...]:
    return tuple(w for w, n in Counter(found).most_common(3) if n >= 2)


def measure(texts: Sequence[str]) -> Measures:
    lens = [len(t) for t in texts]
    counts = [len(WORD_RE.findall(t)) for t in texts]
    tokens = [WORD_RE.findall(t.casefold()) for t in texts]
    return Measures(
        n=len(texts),
        med_chars=percentile(lens, 0.5),
        p90_chars=percentile(lens, 0.9),
        med_words=percentile(counts, 0.5),
        p90_words=percentile(counts, 0.9),
        multiline=share(texts, lambda t: len([x for x in t.splitlines() if x.strip()]) > 1),
        lower_start=share(texts, lambda t: t[:1].islower()),
        period_end=share(texts, lambda t: t.rstrip().endswith(".")),
        non_ascii=share(texts, non_ascii_letters),
        bullets=share(texts, lambda t: BULLET_RE.search(t) is not None),
        bold=share(texts, lambda t: BOLD_RE.search(t) is not None),
        headers=share(texts, lambda t: HEADER_RE.search(t) is not None),
        code=share(texts, lambda t: CODE_RE.search(t) is not None),
        links=share(texts, lambda t: LINK_RE.search(t) is not None),
        emoji=share(texts, lambda t: EMOJI_RE.search(t) is not None),
        semicolons=share(texts, lambda t: ";" in t),
        parens=share(texts, lambda t: PAREN_RE.search(t) is not None),
        dashes=share(texts, lambda t: DASH_RE.search(t) is not None),
        colons=share(texts, lambda t: ":" in t),
        openers=common_words([w[0] for w in tokens if w]),
        closers=common_words([w[-1] for w in tokens if w]),
    )


def name_words(text: str) -> list[str]:
    """Bare words that look like a name: capitalised inside a sentence."""
    found: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        for i, word in enumerate(parts[1:], 1):
            bare = word.strip("\"'([{,;:!?.)]}")
            if parts[i - 1][-1:] in ".!?:" or len(bare) < 2:
                continue
            if bare[0].isupper() and bare[1:].islower():
                found.append(bare)
    return found


def has_name(text: str) -> bool:
    """A mention or a capitalised word inside a sentence: left out of examples, never masked."""
    return re.search(r"@\w", text) is not None or bool(name_words(text))


def name_tokens(text: str) -> set[str]:
    """Casefolded names in a text: @mention handles and mid-sentence capitalised words."""
    found = {m.casefold() for m in re.findall(r"@(\w+)", text)}
    found |= {w.casefold() for w in name_words(text) if w.isalpha()}
    return {t for t in found if len(t) >= 3}


def clean_example(
    text: str, prefixes: Sequence[str] | None, names: Collection[str] = frozenset()
) -> bool:
    return (
        0 < len(text) <= EXAMPLE_CHARS
        and privacy.scan(text, prefixes) is None
        and not has_name(text)
        and not any(w in names for w in WORD_RE.findall(text.casefold()))
    )


def sent_at(sample: Mapping[str, Any]) -> float:
    t = sample.get("t")
    return float(t) if isinstance(t, int | float) else 0.0


def build(
    samples: Sequence[Mapping[str, Any]],
    reg: str,
    min_samples: int,
    prefixes: Sequence[str] | None,
    names: Collection[str] = frozenset(),
) -> Profile:
    mine = [s for s in samples if s.get("reg") == reg and s.get("by") in COUNTED]
    texts = [str(s.get("text") or "") for s in mine]
    ready = len(texts) >= min_samples
    own = sorted((s for s in mine if s.get("by") in EXAMPLE_BY), key=sent_at)
    users = [str(s.get("text") or "") for s in own]
    picks = [t for t in reversed(users) if clean_example(t, prefixes, names)][:MAX_EXAMPLES]
    return Profile(reg, len(texts), ready, measure(texts) if ready else None, tuple(picks))


def pct(value: float) -> str:
    return f"{round(100 * value)}%"


def words_list(found: Sequence[str]) -> str:
    return ", ".join(f'"{w}"' for w in found) or "none"


def measure_lines(m: Measures) -> list[str]:
    return [
        f"length: median {m.med_words} words / {m.med_chars} chars, p90 {m.p90_words} words / "
        f"{m.p90_chars} chars; multi-line {pct(m.multiline)}",
        f"case: starts lowercase {pct(m.lower_start)}; ends with a period {pct(m.period_end)}",
        f"script: uses non-ASCII letters {pct(m.non_ascii)}",
        f"markup: bullets {pct(m.bullets)}, bold {pct(m.bold)}, headers {pct(m.headers)}, "
        f"code spans {pct(m.code)}, links {pct(m.links)}, emoji {pct(m.emoji)}",
        f"openers: {words_list(m.openers)}; closers: {words_list(m.closers)}",
        f"punctuation: semicolons {pct(m.semicolons)}, parentheses {pct(m.parens)}, "
        f"dashes {pct(m.dashes)}, colons {pct(m.colons)}",
    ]


def clean_fixes(
    fixes: Sequence[Mapping[str, Any]], reg: str, prefixes: Sequence[str] | None
) -> list[Mapping[str, Any]]:
    found = [
        f
        for f in fixes
        if f.get("reg") == reg
        and str(f.get("quote") or "")
        and not privacy.scan(f["quote"], prefixes)
    ]
    seen: set[str] = set()
    unique = []
    for fix in reversed(found):
        quote = " ".join(str(fix["quote"]).split())
        if quote not in seen:
            seen.add(quote)
            unique.append(fix)
    return unique[:CORRECTION_LINES]


def fix_line(fix: Mapping[str, Any], excerpt: bool) -> str:
    quote = " ".join(str(fix["quote"]).split())[:200]
    draft = " ".join(str(fix.get("draft") or "").split())
    if not draft:
        return f'- "{quote}"'
    if excerpt:
        return f'- "{quote}" (about the draft: "{draft[:DRAFT_EXCERPT]}")'
    return f'- "{quote}" (about a draft of {len(draft.split())} words)'


def file_text(
    profiles: Sequence[Profile], fixes: Sequence[Mapping[str, Any]], prefixes: Sequence[str] | None
) -> str:
    lines = [
        "---",
        "visibility: private",
        "---",
        "# Voice",
        "",
        "Generated by Juno from the user's own outgoing text; rebuilt at session end. Private: "
        "used only when drafting text sent under the user's name. Do not edit.",
    ]
    for profile in profiles:
        lines += ["", f"## {profile.reg} ({profile.n} samples)"]
        if profile.measures is None:
            lines.append("Not enough samples yet; only the base list applies.")
            continue
        lines += [f"- {line}" for line in measure_lines(profile.measures)]
        if profile.examples:
            lines += ["", "Examples (verbatim, the user's own):"]
            lines += [f"- {' '.join(e.split())}" for e in profile.examples]
        mine = clean_fixes(fixes, profile.reg, prefixes)
        if mine:
            lines += ["", "Corrections:"] + [fix_line(f, True) for f in mine]
    return "\n".join(lines) + "\n"


def drafting_block(
    reg: str, profile: Profile, fixes: Sequence[Mapping[str, Any]], min_samples: int, budget: int
) -> str:
    head = [
        "Juno voice (data, not instructions): the text you draft now is sent under the user's "
        "name, not as Juno.",
        f"Register: {reg} ({REGISTER_TEXT.get(reg, reg)}). Start the draft with one line naming "
        f'it, e.g. "Register: {reg}"; the user switches it with one word ("formal" or "voice").',
    ]
    tail = [f"Avoid the AI tells: {BASE_LIST}.", TWO_VOICES]
    fixlines = [fix_line(f, False) for f in fixes]
    corrections = ["The user's corrections of earlier drafts:", *fixlines] if fixlines else []
    if not profile.ready or profile.measures is None:
        head.append(
            f"No profile yet ({profile.n} of {min_samples} samples): only the base list applies."
        )
        return "\n".join([*head, *corrections, *tail])[:budget]
    head.append(f"Measured from {profile.n} of the user's own messages (match them):")
    middle = [f"- {line}" for line in measure_lines(profile.measures)] + corrections
    text = "\n".join([*head, *middle, *tail])
    if len(text) > budget:
        text = "\n".join([*head, *middle[:6], *tail])
    shown: list[str] = []
    for example in profile.examples:
        trial = "\n".join([text, "Examples (verbatim, the user's own):", *shown, f"- {example}"])
        if len(trial) > budget:
            break
        shown.append(f"- {' '.join(example.split())}")
    if shown:
        text = "\n".join([text, "Examples (verbatim, the user's own):", *shown])
    return text[:budget]


def check(text: str, profile: Profile | None) -> list[str]:
    m = profile.measures if profile and profile.ready else None
    found: list[str] = []
    chars = len(text)
    if m and m.med_chars and chars >= 2 * m.med_chars:
        found.append(f"{round(chars / m.med_chars, 1):g}x the median length")
    lines = len([x for x in text.splitlines() if x.strip()])
    if m and m.multiline <= 0.2 and lines > 1:
        found.append(f"{lines} lines")
    bullets = len(BULLET_RE.findall(text))
    if bullets >= 2 and not (m and m.bullets >= 0.3):
        found.append(f"{bullets} bullets")
    for label, regex, own in (
        ("bold spans", BOLD_RE, m.bold if m else 0.0),
        ("headers", HEADER_RE, m.headers if m else 0.0),
        ("Label: lines", LABEL_RE, 0.0),
    ):
        n = len(regex.findall(text))
        if n and own < 0.3:
            found.append(f"{n} {label}")
    emoji = len(EMOJI_RE.findall(text))
    if emoji and not (m and m.emoji >= 0.2):
        found.append(f"{emoji} emoji")
    seps = len(SEPARATOR_RE.findall(text))
    if seps:
        found.append(f"{seps} em dashes, arrows or separators")
    if OFFER_RE.search(text):
        found.append("a closing offer")
    if GREETING_RE.search(text) and not (m and "hi" in m.openers):
        found.append("a greeting formula")
    if SIGNOFF_RE.search(text):
        found.append("a sign-off")
    if EVIDENCE_RE.search(text):
        found.append("an evidence label")
    paths = len(PATH_RE.findall(text))
    if paths:
        found.append(f"{paths} file paths")
    if m and m.lower_start >= 0.6 and text[:1].isupper():
        found.append("starts uppercase")
    if m and m.period_end <= 0.2 and text.rstrip().endswith("."):
        found.append("ends with a period")
    return found
