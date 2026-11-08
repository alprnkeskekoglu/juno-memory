import re
from collections.abc import Sequence

from juno.core import fold
from juno.core.errors import Refused
from juno.core.redact import find_secrets

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z][\w.-]*")
PHONE_RE = re.compile(r"\+\d[\d ()-]{7,}\d|\b\d{3}[ .-]\d{3}[ .-]?\d{2}[ .-]?\d{2}\b")
LONG_ID_RE = re.compile(r"(?<![\w.])\d{6,}(?!\w|\.\w)")
GIT_HASH_RE = re.compile(
    r"\b(?:commit(?:ted|s)?(?:\s+(?:as|in|at|is))?|sha1?|hash|revision|rev|cherry-pick(?:ed)?"
    r"|revert(?:ed)?|reset|checkout|merged?)\s*[:=]?\s*[`(]?\s*\d{7,12}\b"
    r"|\[[^\]\s]+(?: \([^)\n]*\))? \d{7,12}\]",
    re.I,
)
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
GENERIC_TICKET_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-[0-9]+\b")
PERCENT_RE = re.compile(r"%([0-9A-Fa-f]{2})")


def ticket_re(prefixes: Sequence[str] | None) -> re.Pattern[str]:
    names = [re.escape(p) for p in prefixes or () if p]
    if not names:
        return GENERIC_TICKET_RE
    return re.compile(r"\b(?:" + "|".join(names) + r")-[0-9]+\b", re.I)


def unquote(text: str) -> str:
    return PERCENT_RE.sub(lambda m: chr(int(m.group(1), 16)), text)


def personal_kind(text: str) -> str | None:
    if EMAIL_RE.search(text):
        return "email"
    if PHONE_RE.search(text):
        return "phone"
    if LONG_ID_RE.search(text):
        return "id"
    return None


def url_problem(url: str) -> str | None:
    decoded = unquote(url)
    if EMAIL_RE.search(decoded):
        return "email"
    query = re.split(r"[?#]", decoded, maxsplit=1)
    return personal_kind(query[1]) if len(query) > 1 else None


def personal(text: str, prefixes: Sequence[str] | None = None) -> str | None:
    for url in URL_RE.findall(text):
        problem = url_problem(url)
        if problem:
            return problem
    return personal_kind(GIT_HASH_RE.sub(" ", ticket_re(prefixes).sub(" ", URL_RE.sub(" ", text))))


def scan(text: str | None, prefixes: Sequence[str] | None = None) -> str | None:
    text = text or ""
    return "secret" if find_secrets(text) else personal(text, prefixes)


def kinds(text: str | None, prefixes: Sequence[str] | None = None) -> list[str]:
    text = text or ""
    other = personal(text, prefixes)
    return [*find_secrets(text), *([other] if other else [])]


def check(text: str | None, label: str, prefixes: Sequence[str] | None = None) -> str:
    value = text or ""
    kinds = find_secrets(value)
    if kinds:
        raise Refused(
            f"{label} looks like it contains private data (secret); "
            f"leave it out (kind: {', '.join(kinds)})"
        )
    problem = scan(value, prefixes)
    if problem:
        raise Refused(f"{label} looks like it contains private data ({problem}); leave it out")
    return value


def clean_line(text: str | None, label: str, prefixes: Sequence[str] | None = None) -> str:
    value = " ".join(fold.nfc(text).split())
    if not value:
        raise Refused(f"{label} is empty")
    if "·" in value:
        raise Refused(f"{label} must not contain the '·' separator")
    return check(value, label, prefixes)
