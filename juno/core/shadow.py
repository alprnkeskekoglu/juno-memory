import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from juno.core import redact as redaction

PROMPT_CHARS = 200
HEADING_CHARS = 120
MAX_SUGGESTIONS = 10
MAX_PATHS = 20
SHOWN_SUGGESTIONS = 5
SKIP_TOPS = ("rules/", "protocol/")
SKIP_FILES = ("core.md",)
CARD_HEAD_RE = re.compile(r"^\[Card · ([^\]\n]+)\]$", re.M)

Entry = dict[str, Any]
LOOSE_RES = [re.compile(pattern.replace("\\b", "")) for _, pattern in redaction.SECRET_PATTERNS]


def redact(text: str) -> str:
    text = redaction.redact(text)
    for regex in LOOSE_RES:
        text = regex.sub("[redacted]", text)
    return text


def suggestable(relpath: str) -> bool:
    return relpath not in SKIP_FILES and not relpath.startswith(SKIP_TOPS)


def suggestion(item: Mapping[str, Any]) -> dict[str, Any]:
    path = item.get("path", item.get("relpath"))
    if path is None:
        raise ValueError("suggestion without path")
    return {
        "path": str(path),
        "heading": redact(str(item.get("heading") or ""))[:HEADING_CHARS],
        "score": float(item["score"]),
    }


def unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))[:MAX_PATHS]


def build_entry(
    ts: int,
    segment: str,
    prompt: str,
    suggestions: Sequence[Mapping[str, Any]],
    delivered: Iterable[str] = (),
    carried: Iterable[str] = (),
    pasted: Iterable[str] = (),
    arms: Mapping[str, str] | None = None,
    alt: Iterable[str] = (),
) -> Entry:
    entry: Entry = {
        "ts": ts,
        "segment": str(segment),
        "prompt": redact(prompt)[:PROMPT_CHARS],
        "suggestions": [suggestion(s) for s in list(suggestions)[:MAX_SUGGESTIONS]],
        "delivered": [str(p) for p in list(delivered)[:MAX_PATHS]],
        "carried": [str(p) for p in list(carried)[:MAX_PATHS]],
        "pasted": [str(k) for k in list(pasted)[:MAX_PATHS]],
    }
    alt_paths = [str(p) for p in list(alt)[:SHOWN_SUGGESTIONS]]
    if alt_paths:
        entry["alt"] = alt_paths
    if arms:
        entry["arms"] = {str(k): str(v) for k, v in arms.items()}
    return entry


def dump(entry: Entry) -> str:
    return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))


def valid_entry(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("ts"), (int, float))
        and not isinstance(entry.get("ts"), bool)
        and "segment" in entry
        and "prompt" in entry
    )


def parse_lines(lines: Iterable[str]) -> tuple[list[Entry], int]:
    entries: list[Entry] = []
    bad = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if valid_entry(entry):
            entries.append(entry)
        else:
            bad += 1
    return entries, bad


def dedupe_key(entry: Mapping[str, Any]) -> tuple[int, str, str]:
    prompt = str(entry.get("prompt", ""))
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return int(entry["ts"]), str(entry["segment"]), digest


def merge(existing: Sequence[Entry], incoming: Sequence[Entry]) -> tuple[list[Entry], int, int]:
    seen = {dedupe_key(e) for e in existing}
    added: list[Entry] = []
    skipped = 0
    for entry in incoming:
        key = dedupe_key(entry)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        added.append(entry)
    merged = sorted([*existing, *added], key=lambda e: (e["ts"], str(e["segment"])))
    return merged, len(added), skipped


def delivered_paths(
    tickets: Sequence[str],
    before: Iterable[str],
    after: Iterable[str],
    sources: Mapping[str, Sequence[str]],
    pack_text: str,
) -> tuple[list[str], list[str]]:
    earlier = set(before)
    current = set(after)
    delivered: list[str] = []
    carried: list[str] = []
    for key in tickets:
        name = f"ticket:{key}"
        if name not in current:
            continue
        (carried if name in earlier else delivered).extend(sources.get(key, ()))
    delivered += CARD_HEAD_RE.findall(pack_text)
    return unique(delivered), unique(carried)
