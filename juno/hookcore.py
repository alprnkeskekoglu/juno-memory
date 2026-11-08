import contextlib
import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any

from juno import config
from juno.adapters import env as envmod
from juno.adapters import fs, store

Handler = Callable[[dict[str, Any], config.Config], dict[str, Any] | None]


def read_payload() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except OSError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def context_output(event: str, text: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def log(cfg: config.Config, name: str, line: str) -> None:
    with contextlib.suppress(OSError):
        fs.append_log(
            os.path.join(cfg.state, name), line, cfg.get_int("retention", "error_log_bytes")
        )


_started = time.monotonic()


def started() -> float:
    return _started


def run(event: str, handler: Handler) -> int:
    global _started
    started = _started = time.monotonic()
    cfg: config.Config | None = None
    try:
        if os.environ.get("JUNO_DISABLE") == "1":
            return 0
        cfg = config.load()
        payload = read_payload()
        if not envmod.is_interactive():
            return 0
        out = handler(payload, cfg)
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False))
            sys.stdout.flush()
    except BaseException as exc:
        if cfg is not None:
            store.log_error(cfg, event, exc)
    if cfg is not None:
        elapsed = int((time.monotonic() - started) * 1000)
        log(cfg, "hook-timings.log", f"{event} {elapsed}ms")
    return 0
