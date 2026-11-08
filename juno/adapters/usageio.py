import json
import os
import re
import time
from collections.abc import Iterable
from typing import Any

from juno import config
from juno.adapters import fs, store

USAGE_FILE = "usage.jsonl"
DAY = 86400
TOUCH_TOOLS = ("Read", "Grep", "Glob")
EVENTS = ("read", "card", "cite")
MD_TOKEN_RE = re.compile(r"[~/\w.\-]+\.md\b")


def path(state: str) -> str:
    return os.path.join(state, USAGE_FILE)


def append(state: str, sid: str, rels: Iterable[str], ev: str) -> None:
    now = round(time.time(), 3)
    data = "".join(
        json.dumps({"t": now, "sid8": sid[:8], "rel": rel, "ev": ev}, separators=(",", ":")) + "\n"
        for rel in dict.fromkeys(rels)
    ).encode("utf-8")
    if not data:
        return
    fs.ensure_private_dir(state)
    fd = os.open(path(state), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if os.write(fd, data) != len(data):
            raise OSError("short write to usage log")
    finally:
        os.close(fd)


def vault_relpath(cfg: config.Config, target: str) -> str | None:
    parts = store.vault_rel(cfg, target)
    return "/".join(parts) if parts else None


def record_touch(
    cfg: config.Config, sid: object, tool: object, tool_in: dict[str, Any], cwd: str
) -> None:
    if tool not in TOUCH_TOOLS or not isinstance(sid, str):
        return
    key = "file_path" if tool == "Read" else "path"
    value = tool_in.get(key)
    if not isinstance(value, str) or not value:
        return
    rel = vault_relpath(cfg, store.resolve(value, cwd))
    if rel:
        append(cfg.state, sid, [rel], "read")


def cited_rels(cfg: config.Config, texts: Iterable[str]) -> list[str]:
    found: list[str] = []
    for text in texts:
        for token in MD_TOKEN_RE.findall(text):
            if token.startswith("~") or os.path.isabs(token):
                rel = vault_relpath(cfg, os.path.expanduser(token))
            else:
                rel = vault_relpath(cfg, os.path.join(cfg.vault, token))
            if rel and os.path.isfile(os.path.join(cfg.vault, *rel.split("/"))):
                found.append(rel)
    return found


def read_rows(state: str) -> list[dict[str, Any]]:
    raw = fs.read_bytes(path(state))
    rows: list[dict[str, Any]] = []
    for line in (raw or b"").decode("utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(row, dict)
            and isinstance(row.get("t"), int | float)
            and isinstance(row.get("rel"), str)
            and row.get("ev") in EVENTS
        ):
            rows.append(row)
    return rows


def read_touches(state: str, sid: str) -> list[tuple[float, str]] | None:
    found = [
        (float(r["t"]), str(r["rel"]))
        for r in read_rows(state)
        if r["ev"] == "read" and r.get("sid8") == sid[:8]
    ]
    return found or None


def prune(state: str, days: int, now: float | None = None) -> int:
    rows = read_rows(state)
    raw = fs.read_bytes(path(state))
    if raw is None:
        return 0
    cutoff = (time.time() if now is None else now) - days * DAY
    keep = [r for r in rows if r["t"] >= cutoff]
    if len(keep) != len(raw.splitlines()):
        text = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in keep)
        fs.atomic_write(path(state), text.encode("utf-8"), 0o600)
    return len(rows) - len(keep)
