import json
import os
import time
from typing import Any

from juno import config
from juno.adapters import fs
from juno.core import arms

DIR = "arms"
LOG = "log.jsonl"
SEGMENTS = "segments.jsonl"
DAY = 86400
CACHE: dict[str, tuple[int, dict[str, tuple[str, float]]]] = {}


def arms_dir(cfg: config.Config) -> str:
    return os.path.join(cfg.state, DIR)


def arm_path(cfg: config.Config, name: str) -> str:
    return os.path.join(arms_dir(cfg), f"{name}.json")


def read_arm(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError, ValueError:
        return None
    return data if isinstance(data, dict) else None


def fraction_of(data: dict[str, Any]) -> float | None:
    prereg = data.get("prereg")
    assignment = prereg.get("assignment") if isinstance(prereg, dict) else None
    fraction = assignment.get("fraction") if isinstance(assignment, dict) else None
    if isinstance(fraction, (int, float)) and not isinstance(fraction, bool):
        return float(fraction)
    return None


def names(cfg: config.Config) -> list[str]:
    try:
        files = sorted(os.listdir(arms_dir(cfg)))
    except OSError:
        return []
    return [f[:-5] for f in files if f.endswith(".json") and arms.NAME_RE.match(f[:-5])]


def running(cfg: config.Config) -> dict[str, tuple[str, float]]:
    root = arms_dir(cfg)
    try:
        stamp = os.stat(root).st_mtime_ns
    except OSError:
        return {}
    cached = CACHE.get(root)
    if cached and cached[0] == stamp:
        return cached[1]
    found: dict[str, tuple[str, float]] = {}
    for name in names(cfg):
        data = read_arm(arm_path(cfg, name))
        fraction = fraction_of(data) if data and data.get("status") == "running" else None
        if data and fraction is not None:
            found[name] = (str(data.get("salt", "")), fraction)
    CACHE[root] = (stamp, found)
    return found


def arms_for(cfg: config.Config, sid: str) -> dict[str, str]:
    if not sid:
        return {}
    return {name: arms.assign(salt, sid, frac) for name, (salt, frac) in running(cfg).items()}


def segments_path(cfg: config.Config) -> str:
    return os.path.join(arms_dir(cfg), SEGMENTS)


def read_segments(cfg: config.Config) -> dict[str, dict[str, Any]]:
    raw = fs.read_bytes(segments_path(cfg))
    rows: dict[str, dict[str, Any]] = {}
    for line in (raw or b"").decode("utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("sid"), str):
            rows[row["sid"]] = row
    return rows


def prune_segments(cfg: config.Config, days: int, now: float | None = None) -> int:
    rows = read_segments(cfg)
    cutoff = (time.time() if now is None else now) - days * DAY
    keep = [r for r in rows.values() if float(r.get("t") or 0) >= cutoff]
    lines = len((fs.read_bytes(segments_path(cfg)) or b"").splitlines())
    if lines == len(keep):
        return 0
    text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in keep)
    fs.atomic_write(segments_path(cfg), text.encode("utf-8"), 0o600)
    return lines - len(keep)
