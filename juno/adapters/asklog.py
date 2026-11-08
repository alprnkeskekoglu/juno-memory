import json
import os
import time
from typing import Any

from juno.adapters import fs

ASKS_FILE = "asks.jsonl"
DAY = 86400


def path(state: str) -> str:
    return os.path.join(state, ASKS_FILE)


def append(state: str, sid: str, tid: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fs.ensure_private_dir(state)
    now = round(time.time(), 3)
    data = "".join(
        json.dumps({"t": now, "sid": sid, "tid8": tid[:8], **row}, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")
    fd = os.open(path(state), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if os.write(fd, data) != len(data):
            raise OSError("short write to ask log")
    finally:
        os.close(fd)


def prune(state: str, days: int, now: float | None = None) -> int:
    raw = fs.read_bytes(path(state))
    if raw is None:
        return 0
    cutoff = (time.time() if now is None else now) - days * DAY
    keep: list[str] = []
    lines = raw.decode("utf-8", errors="replace").splitlines()
    for line in lines:
        try:
            t = json.loads(line).get("t")
        except ValueError, AttributeError:
            continue
        if isinstance(t, int | float) and t >= cutoff:
            keep.append(line)
    if len(keep) != len(lines):
        fs.atomic_write(path(state), "".join(line + "\n" for line in keep).encode("utf-8"), 0o600)
    return len(lines) - len(keep)


def read(state: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in (fs.read_bytes(path(state)) or b"").decode("utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("sid"), str):
            rows.append(row)
    return rows
