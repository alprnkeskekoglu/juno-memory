import re

from juno.core import fold

SECOND_PERSON_RE = re.compile(r"(?i)(?<![\w-])you(?:r|rs|rself)?(?![\w-])")
ANCHOR_RE = re.compile(r"[\w./-]{4,}")


def text_field(value: object) -> str:
    return " ".join(fold.nfc(value).split()) if isinstance(value, str) else ""


def as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def anchored(evidence: str, digest: str) -> bool:
    haystack = (digest or "").casefold()
    tokens = [t for t in ANCHOR_RE.findall(evidence) if re.search(r"[\d/._-]", t)]
    return any(t.casefold() in haystack for t in tokens) or (
        " ".join(evidence.split()).casefold() in haystack
    )
