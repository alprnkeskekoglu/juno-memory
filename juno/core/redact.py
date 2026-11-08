import json
import re

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
    r"auth[_-]?token|secret|token|password|passwd)[\"']?\s*[:=]\s*[\"']?)([^\s\"'`,;]+)"
)
TICKET_SHAPE_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{0,9}-[0-9]+")


def assign_value_is_secret(value: str) -> bool:
    if value.startswith(("$", "<", "{", "%", "(")) or TICKET_SHAPE_RE.fullmatch(value):
        return False
    mixed = re.search(r"[0-9]", value) is not None and re.search(r"[A-Za-z]", value) is not None
    return (len(value) >= 12 and mixed) or len(value) >= 20


def find_secrets(text: str | None) -> list[str]:
    text = text or ""
    kinds = [kind for kind, regex in SECRET_RES if regex.search(text)]
    if any(assign_value_is_secret(m.group(2)) for m in ASSIGN_RE.finditer(text)):
        kinds.append("key=value")
    return kinds


def redact(text: str | None) -> str:
    text = text or ""
    for kind, regex in SECRET_RES:
        text = regex.sub(f"[redacted:{kind}]", text)

    def sub(match: re.Match[str]) -> str:
        if assign_value_is_secret(match.group(2)):
            return match.group(1) + "[redacted:key=value]"
        return match.group(0)

    return ASSIGN_RE.sub(sub, text)


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
