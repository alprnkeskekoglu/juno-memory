import re
from collections.abc import Sequence

from juno.core import fold
from juno.core.errors import Refused
from juno.core.redact import find_secrets

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z][\w.-]*")
PHONE_RE = re.compile(r"\+\d[\d ()-]{7,}\d|\b0?\d{3}[ .-]\d{3}[ .-]?\d{2}[ .-]?\d{2}\b")
# IBAN: country + check digits glued to the number, which LONG_ID_RE skips (letters before it).
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z]{4})?(?: ?\d{4}){3,7}(?: ?\d{1,4})?\b")
LONG_ID_RE = re.compile(r"(?<![\w.])\d{6,20}(?!\w|\.\w)")
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
# Object word + id, plural too; ids chained with `,`/`and` must be 6-10 digits without a
# leading zero, so a phone or TCKN cannot ride along.
OBJECT_ID_RE = re.compile(
    r"\b(?:comment|review|issue|run|PR|pull|build|job|rule|alert)s?\s*[#(:=]?\s*\d{6,}"
    r"(?:\s*(?:,|and|&)\s*#?[1-9]\d{5,9}(?!\w))*(?!\w|\.\w)",
    re.I,
)
# The 8-char prompt id on self lines (`src: user 26224046`, `segment 01234567`) can be all digits.
SELF_SRC_RE = re.compile(r"\b(?:src: user|segment) [0-9a-f]{8}\b")
# All-digit short git SHAs, only next to a git anchor: a ref (`origin/x`, `main`, `develop`),
# `branch/x @`, a repo name ending in `repo`, a hex SHA then `to`, or exactly 7 digits in ().
GIT_CONTEXT_RE = re.compile(
    r"\b(?:origin/[\w./-]+\s*[@:]?|(?:main|master|develop|HEAD)|\w*repo)\s*[`(]?\s*\d{7,12}\b"
    r"|\b[\w.-]+/[\w./-]+\s+@\s*\d{7,12}\b"
    r"|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,12}\s+to\s+\d{7,12}\b"
    r"|\(\d{7}\)",
    re.I,
)
# 12-digit AWS account id: after ECR/AWS/`aws account`, or right before a region (`eu-west-1`).
# The bare word `account` does not exempt (bank accounts).
AWS_ACCOUNT_RE = re.compile(
    r"\b(?:ECR|AWS|aws account(?: id)?)\s*[:#]?\s*\d{12}\b"
    r"|(?<![\w.])\d{12}\s+(?:us|eu|ap|sa|ca|me|af|il|mx)-[a-z]+-\d\b",
    re.I,
)
# A plausible date glued to a hyphenated/path name: `home-cleanup-20260101`.
DATE_SUFFIX_RE = re.compile(
    r"(?<=[A-Za-z]-)(?:19|20)\d\d(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?!\w|\.\w)"
)
# `UPPER_CASE_NAME = 123456`, except names that hint at personal data.
CONST_RE = re.compile(
    r"\b(?![A-Z0-9_]*(?:PHONE|TEL|GSM|MOBILE|CELL|TCKN|SSN|IBAN|ACCOUNT|ACCT|CARD|PASS|CUSTOMER"
    r"|USER|PERSON|(?<![A-Z0-9])(?:ID|TC)(?![A-Z0-9])))"
    r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\s*=\s*`?[1-9]\d{5,12}(?!\w|\.\w)"
)
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
# Any `WORD-digits` key, but not prefixes that name personal data and at most 8 digits
# (`TC-12345678901`, `ID-123456789` stay flagged).
PERSONAL_KEY = r"(?:ID|TC|TCKN|SSN|IBAN|PASS|PWD|TEL|PHONE|GSM|CARD|ACC|ACCT|VKN|NIN|SIN|CC)"
GENERIC_TICKET_RE = re.compile(r"\b(?!" + PERSONAL_KEY + r"-)[A-Z][A-Z0-9]{1,9}-[0-9]{1,8}\b")
PERCENT_RE = re.compile(r"%([0-9A-Fa-f]{2})")


def ticket_re(prefixes: Sequence[str] | None) -> re.Pattern[str]:
    names = [re.escape(p) for p in prefixes or () if p]
    if not names:
        return GENERIC_TICKET_RE
    return re.compile(r"\b(?:" + "|".join(names) + r")-[0-9]+\b", re.I)


def exempt_res(prefixes: Sequence[str] | None) -> tuple[re.Pattern[str], ...]:
    """Patterns blanked before the personal checks: ticket keys, commit words, units, object ids."""
    return (
        GIT_CONTEXT_RE,  # git patterns first: they need ref text (`feature/ABC-1 @`) intact
        GIT_HASH_RE,
        ticket_re(prefixes),
        GENERIC_TICKET_RE,
        AWS_ACCOUNT_RE,
        DATE_SUFFIX_RE,
        CONST_RE,
        RUN_ID_RE,
        NUMBER_UNIT_RE,
        OBJECT_ID_RE,
        SELF_SRC_RE,
    )


def unquote(text: str) -> str:
    return PERCENT_RE.sub(lambda m: chr(int(m.group(1), 16)), text)


def personal_kind(text: str) -> str | None:
    if EMAIL_RE.search(text):
        return "email"
    if PHONE_RE.search(text):
        return "phone"
    if LONG_ID_RE.search(text) or IBAN_RE.search(text):
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
    checks = (("email", EMAIL_RE), ("phone", PHONE_RE), ("id", LONG_ID_RE), ("id", IBAN_RE))
    for kind, pattern in checks:
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
