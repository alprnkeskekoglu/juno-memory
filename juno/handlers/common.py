import os
from typing import Any

from juno import config, hookcore
from juno.adapters import ledgerio
from juno.core.inject import trim
from juno.core.ledger import Segment


def main_session(payload: dict[str, Any]) -> bool:
    return not payload.get("agent_id")


def payload_cwd(payload: dict[str, Any]) -> str:
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) and cwd else os.getcwd()


def tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("tool_input")
    return value if isinstance(value, dict) else {}


def output(cfg: config.Config, event: str, text: str) -> dict[str, Any] | None:
    if not text:
        return None
    return hookcore.context_output(
        event, trim(text, cfg.get_int("caps", "hook_output_chars"), "the vault")
    )


def capture_title(cfg: config.Config, seg: Segment, payload: dict[str, Any]) -> None:
    title = payload.get("session_title")
    if isinstance(title, str) and title.strip() and title.strip() != seg.title:
        ledgerio.record(cfg, seg, {"ev": "title", "title": title.strip()[:200]})
