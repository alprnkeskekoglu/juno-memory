"""A line per injected topic, knowledge, procedure or pitfall item, so `juno doctor` can list the
ones fired most often in the last week (a noisy trigger shows up there)."""

import contextlib
import json
import os
import time
from collections import Counter
from typing import Any

from juno import config
from juno.adapters import fs, store

FIRES_FILE = "topic-fires.jsonl"
DAY = 86400
DAYS = 7
SHOWN = 5
PRUNE_BYTES = 256 * 1024


def path(cfg: config.Config) -> str:
    return os.path.join(cfg.state, FIRES_FILE)


def rows(cfg: config.Config) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for line in store.read_text(path(cfg)).splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(row, dict)
            and isinstance(row.get("t"), int | float)
            and isinstance(row.get("name"), str)
        ):
            found.append(row)
    return found


def record(cfg: config.Config, name: str, now: float | None = None) -> None:
    """Append one fire; a state dir that cannot be written never fails the hook."""
    stamp = time.time() if now is None else now
    data = (json.dumps({"t": round(stamp, 3), "name": name}) + "\n").encode()
    with contextlib.suppress(OSError):
        store.state_dir(cfg)
        fd = os.open(path(cfg), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, data)
            big = os.fstat(fd).st_size > PRUNE_BYTES
        finally:
            os.close(fd)
        if big:
            keep = [r for r in rows(cfg) if r["t"] >= stamp - DAYS * DAY]
            text = "".join(json.dumps({"t": r["t"], "name": r["name"]}) + "\n" for r in keep)
            fs.atomic_write(path(cfg), text.encode(), 0o600)


def top(
    cfg: config.Config, now: float | None = None, days: int = DAYS, limit: int = SHOWN
) -> list[tuple[str, int]]:
    cutoff = (time.time() if now is None else now) - days * DAY
    counts = Counter(r["name"] for r in rows(cfg) if r["t"] >= cutoff)
    return sorted(counts.items(), key=lambda p: (-p[1], p[0]))[:limit]
