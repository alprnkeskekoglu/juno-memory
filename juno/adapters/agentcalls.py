import contextlib
import os
import re
import time
from typing import Any

from juno import config
from juno.adapters import fs

TTL_SECONDS = 600
DEFAULT_TIMEOUT = 120.0
GRACE_SECONDS = 30.0
SAFE = re.compile(r"[^A-Za-z0-9_-]")
TOOL = "Bash"


def directory(cfg: config.Config) -> str:
    return os.path.join(cfg.state, "agent-calls")


def marker(cfg: config.Config, payload: dict[str, Any]) -> str | None:
    sid, tid = (
        payload.get("session_id"),
        payload.get("tool_use_id") or "agent-" + str(payload.get("agent_id") or ""),
    )
    if not isinstance(sid, str) or not isinstance(tid, str) or not sid or not tid:
        return None
    name = SAFE.sub("", sid)[:64] + "." + SAFE.sub("", tid)[:64]
    return os.path.join(directory(cfg), name)


def background(payload: dict[str, Any]) -> bool:
    tool_in = payload.get("tool_input")
    return isinstance(tool_in, dict) and tool_in.get("run_in_background") is True


def lifetime(payload: dict[str, Any]) -> float:
    if background(payload):
        return float(TTL_SECONDS)
    tool_in = payload.get("tool_input")
    millis = tool_in.get("timeout") if isinstance(tool_in, dict) else None
    seconds = millis / 1000 if isinstance(millis, (int, float)) and millis > 0 else DEFAULT_TIMEOUT
    return min(seconds + GRACE_SECONDS, float(TTL_SECONDS))


def applies(payload: dict[str, Any]) -> bool:
    return payload.get("tool_name") == TOOL and bool(payload.get("agent_id"))


def mark(cfg: config.Config, payload: dict[str, Any]) -> bool:
    if not applies(payload):
        return True
    path = marker(cfg, payload)
    if path is None:
        return False
    try:
        fs.ensure_private_dir(directory(cfg))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, str(time.time() + lifetime(payload)).encode())
        finally:
            os.close(fd)
    except Exception:
        return False
    return True


def reap(cfg: config.Config, now: float | None = None) -> None:
    stamp = time.time() if now is None else now
    with contextlib.suppress(OSError):
        for name in os.listdir(directory(cfg)):
            path = os.path.join(directory(cfg), name)
            if expires(path) <= stamp:
                with contextlib.suppress(OSError):
                    os.unlink(path)


def clear(cfg: config.Config, payload: dict[str, Any]) -> None:
    path = marker(cfg, payload)
    if path is not None and payload.get("agent_id") and not background(payload):
        with contextlib.suppress(OSError):
            os.unlink(path)
    reap(cfg)


def expires(path: str) -> float:
    try:
        with open(path, encoding="ascii") as handle:
            return float(handle.read(64))
    except OSError, ValueError:
        pass
    try:
        return os.stat(path).st_mtime + TTL_SECONDS
    except OSError:
        return 0.0


def active(cfg: config.Config, sid: str, now: float | None = None) -> bool:
    stamp = time.time() if now is None else now
    prefix = SAFE.sub("", sid)[:64] + "."
    try:
        names = os.listdir(directory(cfg))
    except OSError:
        return False
    return any(
        n.startswith(prefix) and expires(os.path.join(directory(cfg), n)) > stamp for n in names
    )
