import json
import os
import time
from collections.abc import Iterable
from typing import Any

from juno.adapters import fs

FILE = "keys.jsonl"


def keys_path(state: str) -> str:
    return os.path.join(state, FILE)


def signature(path: str) -> str:
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return "none"
    return f"{st.st_size}:{st.st_mtime_ns}"


def load_rows(path: str) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except FileNotFoundError:
        return found
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(row, dict)
            and isinstance(row.get("h"), str)
            and isinstance(row.get("facts"), list)
        ):
            found[row["h"]] = {**row, "facts": [f for f in row["facts"] if isinstance(f, str)]}
    return found


def load(path: str) -> dict[str, list[str]]:
    return {h: row["facts"] for h, row in load_rows(path).items()}


def row(
    h: str,
    rel: str,
    facts: list[str],
    by: str,
    model: str,
    why: str = "",
    dropped: int = 0,
    retried: bool = False,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "h": h,
        "rel": rel,
        "facts": facts,
        "by": by,
        "model": model,
        "t": int(time.time()),
    }
    if not facts:
        out["why"] = why or "none"
    if dropped:
        out["dropped"] = dropped
    if retried:
        out["retried"] = True
    return out


def append(state: str, rows: Iterable[dict[str, Any]]) -> None:
    data = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    if not data:
        return
    fs.ensure_private_dir(state)
    blob = data.encode("utf-8")
    fd = os.open(keys_path(state), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if os.write(fd, blob) != len(blob):
            raise OSError("short write to keys.jsonl")
    finally:
        os.close(fd)
