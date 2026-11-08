from typing import Any

from juno import config
from juno.adapters import agentcalls, ledgerio


def error_class(payload: dict[str, Any]) -> str:
    if payload.get("is_interrupt"):
        return "interrupt"
    error = str(payload.get("error") or "").casefold()
    if "timed out" in error or "timeout" in error:
        return "timeout"
    if "permission" in error or "denied" in error:
        return "denied"
    if "exit code" in error or "exited with" in error:
        return "exit"
    return "other"


def handle(payload: dict[str, Any], cfg: config.Config) -> dict[str, Any] | None:
    agentcalls.clear(cfg, payload)
    ledgerio.append(
        cfg,
        payload.get("session_id"),
        {
            "ev": "fail",
            "tool": str(payload.get("tool_name") or "-")[:80],
            "class": error_class(payload),
            "agent": bool(payload.get("agent_id")),
        },
    )
    return None
