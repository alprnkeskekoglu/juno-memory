import contextlib
import errno
import json
import os
import sys
import time
from collections.abc import Callable
from typing import Any

from juno import config
from juno.adapters import env as envmod
from juno.adapters import fs, steps, store
from juno.core import ledger

Handler = Callable[[dict[str, Any], config.Config], dict[str, Any] | None]


STATE_ERRNOS = frozenset({errno.ENOSPC, errno.EACCES, errno.EPERM, errno.EROFS})
STATE_LINE = (
    "Juno: could not write its state dir (disk full or no permission), so handoff tracking "
    "is off this session."
)


def state_unwritable(cfg: config.Config, exc: OSError) -> bool:
    """True for an OSError that means the state dir cannot be written, not a vault problem."""
    if exc.errno not in STATE_ERRNOS:
        return False
    name = exc.filename
    return not isinstance(name, str) or os.path.abspath(name).startswith(cfg.state)


def scrubbed(value: Any) -> Any:
    if isinstance(value, str):
        return ledger.scrub(value)
    if isinstance(value, list):
        return [scrubbed(v) for v in value]
    if isinstance(value, dict):
        return {ledger.scrub(str(k)): scrubbed(v) for k, v in value.items()}
    return value


def read_payload() -> dict[str, Any]:
    try:
        stream = getattr(sys.stdin, "buffer", None)
        raw = stream.read().decode("utf-8", "replace") if stream else sys.stdin.read()
        data = scrubbed(json.loads(raw)) if raw.strip() else {}
    except OSError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def context_output(event: str, text: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def emit(out: dict[str, Any]) -> None:
    text = ledger.scrub(json.dumps(out, ensure_ascii=False))
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        sys.stdout.flush()
        sys.stdout.buffer.write(text.encode("utf-8", "replace"))
    sys.stdout.flush()


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
    steps.drain()
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
            emit(out)
    except BaseException as exc:
        if cfg is not None:
            store.log_error(cfg, event, exc)
    if cfg is not None:
        elapsed = int((time.monotonic() - started) * 1000)
        lines = [
            f"{event} {elapsed}ms",
            *(f"{event}:{n} {ms}ms" for n, ms in steps.drain().items()),
        ]
        log(cfg, "hook-timings.log", "\n".join(lines))
    return 0
