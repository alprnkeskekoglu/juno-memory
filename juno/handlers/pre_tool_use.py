from typing import Any

from juno import config
from juno.adapters import agentcalls, ledgerio, store, topicsio
from juno.core import toolrec, vaultfmt, vaultroute
from juno.handlers.common import main_session, output, payload_cwd, tool_input


def record_ask(cfg: config.Config, payload: dict[str, Any]) -> None:
    tid = payload.get("tool_use_id")
    if not tid or not main_session(payload):
        return
    tool_in = tool_input(payload)
    clean = not any(tool_in.get(key) for key in ("answers", "annotations"))
    ledgerio.append(
        cfg, payload.get("session_id"), {"ev": "ask", "tid": str(tid)[:64], "clean": clean}
    )


STATE_GUARDED = ("ledgers", "cards", "agent-calls", "writes.jsonl")


def deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def vault_refusal(cfg: config.Config, payload: dict[str, Any]) -> dict[str, Any] | None:
    path = toolrec.write_target(tool_input(payload))
    if not path:
        return None
    state: list[str] | None = None
    try:
        real = store.resolve(path, payload_cwd(payload))
        parts = store.vault_rel(cfg, real)
        state = store.parts_under(real, cfg.state)
    except ValueError:
        parts = []
    tool = payload["tool_name"]
    if state and state[0].casefold().startswith(STATE_GUARDED):
        return deny(
            f"Juno: refused: {tool} does not change Juno's own state ({state[0]}); "
            "ledgers, cards, agent-calls and the write log are written by Juno only."
        )
    if parts is None:
        return None
    rel = "/".join(parts)
    hint = vaultroute.hint_for(vaultfmt.kind_of(rel) if rel else "")
    return deny(
        f"Juno: refused: {rel or 'the vault'} is changed with a juno verb, not {tool}: "
        f"use `{hint}` (an existing line: `juno fix {rel or '<file>'}:<line>`); see core.md."
    )


def handle(payload: dict[str, Any], cfg: config.Config) -> dict[str, Any] | None:
    tool = payload.get("tool_name")
    if not agentcalls.mark(cfg, payload):
        return deny(
            "Juno: refused: this subagent call cannot be recorded in the state dir, "
            "so Juno cannot tell the main session's writes from it."
        )
    if tool == toolrec.ASK_TOOL:
        record_ask(cfg, payload)
    elif tool in toolrec.WRITE_TOOLS:
        refusal = vault_refusal(cfg, payload)
        if refusal:
            return refusal
    text = topicsio.for_tool(cfg, payload, cfg.get_int("caps", "hook_output_chars"))
    return output(cfg, "PreToolUse", text)
