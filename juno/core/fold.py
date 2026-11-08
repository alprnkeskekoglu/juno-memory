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
MIN_WORD = 4


def nfc(text: str | None) -> str:
    return unicodedata.normalize("NFC", text or "")


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
