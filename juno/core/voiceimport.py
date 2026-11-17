"""Writing-voice import (12.13): parse a file of the user's own past messages and filter it.

Pure functions; the state files and the profile are written by `adapters/voiceimport.py`."""

import hashlib
import json
import re
import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from juno.core import privacy, voicecollect
from juno.core.redact import redact

REGISTERS = ("voice", "formal")
TARGETS = ("chat", "mail", "tracker", "review", "code")
FORMAL_TARGETS = frozenset({"tracker", "mail", "code"})
REASONS = ("privacy", "short", "quoted", "duplicate", "too_old", "over_limit")
TEXT_BYTES = 1200
MAX_INPUT_BYTES = 20_000_000
DAY = 86400.0
FORWARD_RE = re.compile(
    r"^\s*(?:-{3,}\s*forwarded message|begin forwarded message:|-{3,}\s*original message\s*-{3,}"
    r"|-{3,}\s*iletilen mesaj)",
    re.MULTILINE,
)


@dataclass
class Message:
    text: str
    register: str | None = None
    target: str | None = None
    t: float | None = None


@dataclass
class Plan:
    """The outcome of one import: the rows to add and the per-register counts."""

    batch: str = ""
    rows: list[dict[str, Any]] = field(default_factory=list)
    accepted: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, dict[str, int]] = field(default_factory=dict)


def parse_time(raw: object) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return float(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        when = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    return (when if when.tzinfo else when.replace(tzinfo=UTC)).timestamp()


def jsonl_rows(raw: str) -> list[dict[str, Any]] | None:
    """The objects of a JSONL input, or None unless every non-empty line is an object with a
    `text` string."""
    found: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return None
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            return None
        found.append(row)
    return found or None


def parse(raw: str, register: str | None, target: str | None) -> list[Message]:
    """JSONL when every non-empty line is a JSON object with a `text`, else one message per
    blank-line-separated paragraph. Row values override the flags."""
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    rows = jsonl_rows(raw)
    if rows is None:
        parts = (p.strip() for p in re.split(r"\n[ \t]*\n", raw))
        return [Message(p, register, target) for p in parts if p]
    out: list[Message] = []
    for row in rows:
        text = str(row["text"]).strip()
        reg, kind = row.get("register"), row.get("target")
        out.append(
            Message(
                text,
                reg if reg in REGISTERS else register,
                kind if kind in TARGETS else target,
                parse_time(row.get("t")),
            )
        )
    return out


def register_of(msg: Message) -> str:
    if msg.register:
        return msg.register
    return "formal" if msg.target in FORMAL_TARGETS else "voice"


def is_quoted(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines and sum(ln.lstrip().startswith(">") for ln in lines) * 2 > len(lines):
        return True
    return FORWARD_RE.search(text.casefold().replace("̇", "")) is not None


def reason_to_skip(text: str, prefixes: Sequence[str] | None) -> str | None:
    if is_quoted(text):
        return "quoted"
    if len(text) < voicecollect.MIN_CHARS:
        return "short"
    if privacy.scan(text, prefixes):
        return "privacy"
    return None


def row_id(rec: dict[str, Any]) -> str:
    """Same scheme as `voiceio.sample_id`, with a fixed session so a repeat import matches."""
    raw = f"import|sample|{rec.get('t')}|{rec.get('text')}"
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:16]


def plan(
    messages: Sequence[Message],
    known_texts: Collection[str],
    prefixes: Sequence[str] | None,
    import_max: int,
    max_age_days: int,
    now: float | None = None,
) -> Plan:
    """Skip, never mask. Newest first by `t`, so the limit keeps the newest messages. The batch
    id is a digest of the accepted texts: a dry run and the real run name the same batch."""
    now = time.time() if now is None else now
    skipped: dict[str, dict[str, int]] = {r: dict.fromkeys(REASONS, 0) for r in REGISTERS}
    accepted = dict.fromkeys(REGISTERS, 0)
    seen = set(known_texts)
    stamped = [(min(m.t if m.t is not None else now, now), i, m) for i, m in enumerate(messages)]
    stamped.sort(key=lambda item: item[:2], reverse=True)  # ties: a later line is newer
    kept: list[dict[str, Any]] = []
    for when, _, msg in stamped:
        reg = register_of(msg)
        text = voicecollect.clip(redact(msg.text), TEXT_BYTES)
        why = reason_to_skip(msg.text, prefixes)
        if why is None and now - when > max_age_days * DAY:
            why = "too_old"
        if why is None and text in seen:
            why = "duplicate"
        if why is None and len(kept) >= import_max:
            why = "over_limit"
        if why:
            skipped[reg][why] += 1
            continue
        seen.add(text)
        accepted[reg] += 1
        rec = {
            "k": "sample",
            "t": when,
            "reg": reg,
            "kind": msg.target or "chat",
            "by": "import",
            "text": text,
        }
        rec["id"] = row_id(rec)
        kept.append(rec)
    kept.reverse()
    digest = hashlib.sha1("\n".join(r["text"] for r in kept).encode(), usedforsecurity=False)
    batch = digest.hexdigest()[:8]
    for rec in kept:
        rec["sid"] = f"import:{batch}"
    return Plan(batch, kept, accepted, skipped)
