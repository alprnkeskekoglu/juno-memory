import copy
import json
import os
import time
from typing import Any

from juno.adapters import fs
from juno.core import dashboard_usage as usage
from juno.core.dashboard_prices import PRICES

CACHE_VERSION = 2
PRICES_OVERRIDE = "prices.json"


def dashboard_dir(state: str, *parts: str) -> str:
    return os.path.join(state, "dashboard", *parts)


def write_json(path: str, data: Any) -> None:
    fs.ensure_private_dir(os.path.dirname(path))
    fs.atomic_write(path, (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8"), 0o600)


def load_prices(state: str) -> usage.Prices:
    raw = fs.read_bytes(os.path.join(state, PRICES_OVERRIDE))
    if raw is None:
        return copy.deepcopy(PRICES)
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data.get("models"), dict):
            raise ValueError("no models table")
    except (ValueError, AttributeError) as error:
        raise ValueError(f"prices.json unreadable ({type(error).__name__})") from error
    found: usage.Prices = data
    return found


def load_cache(state: str) -> dict[str, Any]:
    raw = fs.read_bytes(dashboard_dir(state, "usage.json"))
    if raw is not None:
        try:
            data = json.loads(raw.decode("utf-8"))
            if (
                data.get("v") == CACHE_VERSION
                and isinstance(data.get("files"), dict)
                and isinstance(data.get("msgs"), dict)
                and isinstance(data.get("strs"), list)
            ):
                found: dict[str, Any] = data
                return found
        except ValueError, AttributeError:
            pass
    return {"v": CACHE_VERSION, "files": {}, "msgs": {}, "strs": []}


def save_cache(state: str, cache: dict[str, Any]) -> None:
    fs.ensure_private_dir(dashboard_dir(state))
    text = json.dumps(cache, separators=(",", ":")) + "\n"
    fs.atomic_write(dashboard_dir(state, "usage.json"), text.encode("utf-8"), 0o600)


def scan_file(
    path: str, start: int, msgs: dict[str, Any], intern: usage.Interner, floor: float
) -> int:
    with open(path, "rb") as handle:
        handle.seek(start)
        data = handle.read()
    end = data.rfind(b"\n") + 1
    for line in data[:end].split(b"\n"):
        if b'"usage"' not in line or b'"assistant"' not in line:
            continue
        try:
            found = usage.usage_row(json.loads(line), path, intern, floor)
        except ValueError, KeyError, TypeError, AttributeError:
            continue
        if found and (found[0] not in msgs or found[1][3] >= msgs[found[0]][3]):
            msgs[found[0]] = found[1]
    return start + end


def collect(state: str, root: str, now: float) -> tuple[dict[str, Any], dict[str, Any]]:
    """Incrementally scan transcripts under `root`; returns (cache, stats). Saves nothing."""
    started = time.monotonic()
    cache = load_cache(state)
    intern = usage.Interner(cache["strs"])
    floor = usage.day_start(usage.window_days(usage.LOOKBACK_DAYS, now)[0])
    files: dict[str, Any] = {}
    stats: dict[str, Any] = {"files": 0, "scanned": 0, "rescanned": 0, "bytes": 0}
    for folder, _, names in os.walk(root):
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(folder, name)
            try:
                info = os.stat(path)
            except OSError:
                continue
            if info.st_mtime < floor:
                continue
            stats["files"] += 1
            prev = cache["files"].get(path)
            start = usage.plan_start(prev, info.st_size, info.st_mtime_ns)
            offset = (prev or {}).get("offset", 0)
            if start is not None:
                try:
                    offset = scan_file(path, start, cache["msgs"], intern, floor)
                except OSError:
                    continue
                stats["scanned"] += 1
                stats["rescanned"] += bool(prev and start == 0)
                stats["bytes"] += max(0, offset - start)
            files[path] = {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "offset": offset}
    cache["files"] = files
    cache["msgs"] = {m: row for m, row in cache["msgs"].items() if row[0] >= floor}
    stats["seconds"] = round(time.monotonic() - started, 2)
    return cache, stats
