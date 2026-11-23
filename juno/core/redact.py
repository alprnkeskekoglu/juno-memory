import base64
import binascii
import json
import re
import unicodedata
from collections.abc import Callable

CHUNK_BYTES = 3000

SECRET_PATTERNS = [
    ("private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("github", r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,})"),
    ("aws", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("slack", r"\bxox[abposr]-[A-Za-z0-9-]{10,}|hooks\.slack\.com/services/[A-Za-z0-9/]{20,}"),
    ("stripe", r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"),
    ("google", r"\bAIza[0-9A-Za-z_-]{35}|\bya29\.[0-9A-Za-z_-]{20,}"),
    ("npm", r"\bnpm_[A-Za-z0-9]{36}\b"),
    ("sendgrid", r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("bearer", r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    ("sk", r"\bsk-[A-Za-z0-9_-]{20,}"),
]
SECRET_RES = [(kind, re.compile(pattern)) for kind, pattern in SECRET_PATTERNS]
ASSIGN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:api[_-]?key|access[_-]?key|secret[_-]?key|client[_-]?secret|"
    r"private[_-]?key|auth[_-]?token|secret|token|password|passwd)[\"']?\]?\s*[:=]\s*[\"']?)([^\s\"'`,;]+)"
)
SETX_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])((?:setx|set\s+(?:-[A-Za-z]+\s+)*-[A-Za-z]*x[A-Za-z]*)\s+[\"']?"
    r"[A-Za-z0-9_]*(?:api[_-]?key|access[_-]?key|secret[_-]?key|client[_-]?secret|private[_-]?key|"
    r"auth[_-]?token|secret|token|password|passwd)[\"']?\s+[\"']?)([^\s\"'`,;]+)"
)
BASIC_RE = re.compile(r"\b(Basic\s+)([A-Za-z0-9+/]{12,}={0,2})(?![A-Za-z0-9+/=])")
TICKET_SHAPE_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{0,9}-[0-9]+")


def assign_value_is_secret(value: str) -> bool:
    if value.startswith(("$", "<", "{", "%", "(")) or TICKET_SHAPE_RE.fullmatch(value):
        return False
    mixed = re.search(r"[0-9]", value) is not None and re.search(r"[A-Za-z]", value) is not None
    return (len(value) >= 12 and mixed) or len(value) >= 20


def basic_value_is_secret(value: str) -> bool:
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), validate=True).decode("ascii")
    except binascii.Error, UnicodeDecodeError:
        return False
    return re.fullmatch(r"[\w.@+-]{1,64}:[\x21-\x7e]{4,}", decoded) is not None


ASSIGN_KINDS = (
    ("key=value", ASSIGN_RE, assign_value_is_secret),
    ("key=value", SETX_RE, assign_value_is_secret),
    ("basic-auth", BASIC_RE, basic_value_is_secret),
)


def strip_format(text: str) -> str:
    """Drop Unicode format characters (Cf: zero-width, bidi marks) that can hide a secret."""
    return "".join(c for c in text if unicodedata.category(c) != "Cf")


def find_secrets(text: str | None) -> list[str]:
    text = strip_format(text or "")
    kinds = [kind for kind, regex in SECRET_RES if regex.search(text)]
    for kind, regex, is_secret in ASSIGN_KINDS:
        if kind not in kinds and any(is_secret(m.group(2)) for m in regex.finditer(text)):
            kinds.append(kind)
    return kinds


def redact(text: str | None) -> str:
    text = text or ""
    stripped = strip_format(text)
    if stripped != text and find_secrets(stripped):
        text = stripped
    for kind, regex in SECRET_RES:
        text = regex.sub(f"[redacted:{kind}]", text)
    for kind, regex, is_secret in ASSIGN_KINDS:

        def sub(
            match: re.Match[str], kind: str = kind, is_secret: Callable[[str], bool] = is_secret
        ) -> str:
            if is_secret(match.group(2)):
                return match.group(1) + f"[redacted:{kind}]"
            return match.group(0)

        text = regex.sub(sub, text)
    return text


def chunks(text: str, limit: int = CHUNK_BYTES) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(text):
        n = 1500
        while n > 1 and len(json.dumps(text[i : i + n], ensure_ascii=False).encode()) > limit:
            n //= 2
        out.append(text[i : i + n])
        i += n
    return out or [""]
