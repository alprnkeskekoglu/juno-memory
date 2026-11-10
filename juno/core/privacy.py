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
RUN_ID_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-20\d{2}(?:0[1-9]|1[0-2])\d{2,8}\b")
NUMBER_UNIT_RE = re.compile(
    r"(?<![\w.])\d{6,} ?(?:bytes|B|KB|MB|GB|ms|s|sec|seconds)(?!\w)", re.I
)
OBJECT_ID_RE = re.compile(
    r"\b(?:comment|review|issue|run|PR|pull|build|job) ?#?\d{6,}(?!\w|\.\w)", re.I
)
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
GENERIC_TICKET_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-[0-9]+\b")
PERCENT_RE = re.compile(r"%([0-9A-Fa-f]{2})")


def ticket_re(prefixes: Sequence[str] | None) -> re.Pattern[str]:
    names = [re.escape(p) for p in prefixes or () if p]
    if not names:
        return GENERIC_TICKET_RE
    return re.compile(r"\b(?:" + "|".join(names) + r")-[0-9]+\b", re.I)


def exempt_res(prefixes: Sequence[str] | None) -> tuple[re.Pattern[str], ...]:
    """Patterns blanked before the personal checks: ticket keys, commit words, units, object ids."""
    return (ticket_re(prefixes), GIT_HASH_RE, RUN_ID_RE, NUMBER_UNIT_RE, OBJECT_ID_RE)


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
    text = URL_RE.sub(" ", text)
    for pattern in exempt_res(prefixes):
        text = pattern.sub(" ", text)
    return personal_kind(text)


PLACEHOLDERS = {"id": "<id>", "phone": "<phone>", "email": "<email>"}
MASK_PASSES = 3


def blank(chars: list[tuple[str, int]], pattern: re.Pattern[str]) -> list[tuple[str, int]]:
    """Blank every match of pattern the way personal() does (one space), keeping origins."""
    text = "".join(c for c, _ in chars)
    out: list[tuple[str, int]] = []
    pos = 0
    for m in pattern.finditer(text):
        out += chars[pos : m.start()] + [(" ", -1)]
        pos = m.end()
    return out + chars[pos:]


def personal_spans(
    text: str, prefixes: Sequence[str] | None = None
) -> list[tuple[int, int, str]]:
    """(start, end, kind) of every span personal() would object to, in text offsets."""
    spans: list[tuple[int, int, str]] = []
    for m in URL_RE.finditer(text):
        problem = url_problem(m.group(0))
        if problem:
            spans.append((m.start(), m.end(), problem))
    chars = [(c, i) for i, c in enumerate(text)]
    for pattern in (URL_RE, *exempt_res(prefixes)):
        chars = blank(chars, pattern)
    for kind, pattern in (("email", EMAIL_RE), ("phone", PHONE_RE), ("id", LONG_ID_RE)):
        flat = "".join(c for c, _ in chars)
        hits = [(m.start(), m.end()) for m in pattern.finditer(flat)]
        for a, b in hits:
            real = [i for _, i in chars[a:b] if i >= 0]
            if real:
                spans.append((min(real), max(real) + 1, kind))
        chars = blank(chars, pattern)
    return spans


def mask(text: str, prefixes: Sequence[str] | None = None) -> tuple[str, dict[str, int]]:
    """Replace personal spans with <id>, <phone>, <email>; return the text and counts by kind.

    Secrets are not handled here. Text with no personal data comes back unchanged."""
    counts: dict[str, int] = {}
    for _ in range(MASK_PASSES):
        spans = sorted(personal_spans(text, prefixes))
        if not spans:
            break
        out: list[str] = []
        pos = 0
        for start, end, kind in spans:
            if start < pos:
                continue
            out += [text[pos:start], PLACEHOLDERS[kind]]
            pos = end
            counts[kind] = counts.get(kind, 0) + 1
        text = "".join(out) + text[pos:]
    return text, counts


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
