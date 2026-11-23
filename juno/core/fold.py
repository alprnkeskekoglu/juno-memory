import re
import unicodedata
from collections.abc import Iterable

TR_MAP = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "ş": "s",
        "Ş": "s",
        "ğ": "g",
        "Ğ": "g",
        "ü": "u",
        "Ü": "u",
        "ö": "o",
        "Ö": "o",
        "ç": "c",
        "Ç": "c",
    }
)
TR_LOWER = str.maketrans({"I": "ı", "İ": "i"})
TR_LETTERS = frozenset("çÇğĞıİöÖşŞüÜ")
MIN_WORD = 4


def nfc(text: str | None) -> str:
    return unicodedata.normalize("NFC", text or "")


def tr_lower(text: str | None) -> str:
    """Lowercase; a bare capital I turns dotless only when the text has another Turkish letter."""
    value = nfc(text)
    if TR_LETTERS.isdisjoint(value):
        return value.lower()
    return value.translate(TR_LOWER).lower()


def fold(text: str | None, preset: str = "none") -> str:
    value = nfc(text)
    value = value.translate(TR_MAP).lower() if preset == "tr" else value.casefold()
    return " ".join(value.split())


def ascii_fold(text: str | None, preset: str = "none") -> str:
    value = nfc(text)
    value = value.translate(TR_MAP) if preset == "tr" else value
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def slug(text: str | None, preset: str = "none") -> str:
    folded = ascii_fold(text, preset)
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]", "-", folded)).strip("-")[:60].strip("-")


def safe_name(name: str | None) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", name or "").lstrip(".-")
    return cleaned[:80] or "misc"


def meaningful_words(text: str, preset: str, stopwords: Iterable[str]) -> set[str]:
    stop = {fold(word, preset) for word in stopwords}
    return {
        word
        for word in re.findall(r"\w+", fold(text, preset))
        if len(word) >= MIN_WORD and word not in stop
    }


def quote_problem(
    quote: str, rule: str, preset: str, stopwords: Iterable[str], min_chars: int
) -> str | None:
    collapsed = " ".join(nfc(quote).split())
    if len(collapsed) < min_chars:
        return f"quote is shorter than {min_chars} characters"
    shared = meaningful_words(collapsed, preset, stopwords) & meaningful_words(
        rule, preset, stopwords
    )
    return None if shared else "quote shares no meaningful word with the rule text"


def has_cue(text: str, cues: Iterable[str], preset: str) -> bool:
    folded = fold(text, preset).replace("’", "'")
    return any(
        re.search(r"(?<![a-z0-9])" + re.escape(fold(cue, preset).replace("’", "'")), folded)
        for cue in cues
    )


# Turkish case and plural endings after `fold` (dotless i, u-umlaut and the rest are ASCII by then).
TR_SUFFIXES = tuple(
    sorted(
        (
            "lar ler in un nin nun a e ya ye na ne i u yi yu ni nu "
            "da de ta te nda nde dan den tan ten ndan nden"
        ).split(),
        key=len,
        reverse=True,
    )
)
MIN_STEM = 3
MAX_STRIPS = 3
WORD_RE = re.compile(r"[\w'’]+")


def strip_suffixes(word: str, strips: int = MAX_STRIPS) -> list[str]:
    found: list[str] = []
    if strips <= 0:
        return found
    for suffix in TR_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= MIN_STEM:
            stem = word[: -len(suffix)]
            found.append(stem)
            found += strip_suffixes(stem, strips - 1)
    return found


def stems(folded: str, preset: str) -> list[str]:
    """Stems of the words of an already folded text: a light suffix strip for agglutinative
    languages (only `tr` today); nothing for any other preset."""
    if preset != "tr":
        return []
    found: dict[str, None] = {}
    for word in WORD_RE.findall(folded):
        base, quote, _ = word.partition("'") if "'" in word else word.partition("’")
        if quote and len(base) >= 2:
            found[base] = None
        for stem in strip_suffixes(base):
            found[stem] = None
    return list(found)
