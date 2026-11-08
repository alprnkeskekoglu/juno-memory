import json
import os
import uuid

from juno import config
from juno.adapters import fs

MAX_BYTES = 2 * 1024 * 1024


def path(cfg: config.Config) -> str:
    return os.path.join(cfg.state, "writes.jsonl")


def new_id() -> str:
    return "w-" + uuid.uuid4().hex[:8]


def append(cfg: config.Config, rec: dict[str, object]) -> None:
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    fs.append_log(path(cfg), line, MAX_BYTES)


def read(cfg: config.Config) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for name in (path(cfg) + ".1", path(cfg)):
        raw = fs.read_bytes(name)
        for line in (raw or b"").decode("utf-8", "replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                found.append(rec)
    return found


def undone(records: list[dict[str, object]]) -> set[str]:
    return {str(r["of"]) for r in records if r.get("verb") == "undo" and r.get("of")}


def live_count(records: list[dict[str, object]], sid: str) -> int:
    gone = undone(records)
    return sum(
        1
        for r in records
        if r.get("sid") == sid
        and r.get("verb") != "undo"
        and not r.get("engine")
        and r.get("id") not in gone
    )
