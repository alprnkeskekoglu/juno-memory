from collections.abc import Mapping

from juno import config
from juno.adapters import agentcalls, ledgerio
from juno.adapters import env as envmod


def write_refusal(cfg: config.Config, env: Mapping[str, str] | None = None) -> str | None:
    env = cfg.env if env is None else env
    if not envmod.is_interactive(env):
        return "not an interactive session"
    if envmod.agent_context(env):
        return "agent context"
    sid = env.get("CLAUDE_CODE_SESSION_ID", "")
    if sid and agentcalls.active(cfg, sid):
        return "agent context: a subagent tool call is in flight"
    if not sid or not ledgerio.exists(cfg, sid):
        return "no session ledger"
    if ledgerio.fold(cfg, sid).nosave:
        return "[nosave] is set in this segment"
    return None


def refusal_line(reason: str) -> str:
    return f"Juno: refused: {reason}"
