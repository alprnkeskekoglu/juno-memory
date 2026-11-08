import json
import os
import time
from typing import Any

from juno import config
from juno.adapters import fs

BLOCK_CHARS = 400
MAX_BYTES = 1 << 22


def path(cfg: config.Config) -> str:
    return os.path.join(cfg.state, "cards", "next.jsonl")


def append(cfg: config.Config, seg8: str, rel: str, pairs: list[dict[str, str]]) -> None:
    row = {"t": int(time.time()), "seg": seg8, "file": rel, "pairs": pairs}
    fs.append_log(path(cfg), json.dumps(row, ensure_ascii=False, sort_keys=True), MAX_BYTES)


def load(cfg: config.Config) -> dict[str, list[dict[str, str]]]:
    raw = fs.read_bytes(path(cfg))
    found: dict[str, list[dict[str, str]]] = {}
    for line in (raw or b"").decode("utf-8", errors="replace").splitlines():
        try:
            row: Any = json.loads(line)
        except ValueError:
            continue
        pairs = row.get("pairs") if isinstance(row, dict) else None
        if isinstance(row.get("seg") if isinstance(row, dict) else None, str) and isinstance(
            pairs, list
        ):
            found[row["seg"]] = [
                {"q": str(p["q"]), "a": str(p["a"])}
                for p in pairs
                if isinstance(p, dict) and "q" in p and "a" in p
            ]
    return found


def block(pairs: list[dict[str, str]]) -> str:
    lines: list[str] = []
    used = 0
    for pair in pairs:
        line = f"- {pair['q']} -> {pair['a']}"
        if used + len(line) + 1 > BLOCK_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)
