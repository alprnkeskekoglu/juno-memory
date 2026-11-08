from collections.abc import Mapping

from juno import config
from juno.adapters import agentcalls, cardjob, ledgerio, vault
from juno.adapters import env as envmod

JOB_PREFIX = "job-"
JOB_MIN_SCHEMA = 2


def write_refusal(cfg: config.Config, env: Mapping[str, str] | None = None) -> str | None:
    env = cfg.env if env is None else env
    if not envmod.is_interactive(env):
        return "not an interactive session"
    if envmod.agent_context(env):
        return "agent context"
    sid = env.get("CLAUDE_CODE_SESSION_ID", "")
    if sid.startswith(JOB_PREFIX):
        return "a job identity cannot write interactively"
    if sid and agentcalls.active(cfg, sid):
        return "agent context: a subagent tool call is in flight"
    if not sid or not ledgerio.exists(cfg, sid):
        return "no session ledger"
    if ledgerio.fold(cfg, sid).nosave:
        return "[nosave] is set in this segment"
    return None


def enabled_refusal(cfg: config.Config) -> str | None:
    if not cfg.get_bool("stale", "auto_fix"):
        return "[stale] auto_fix is off"
    schema = vault.schema_version(cfg.vault)
    if schema is None or schema < JOB_MIN_SCHEMA:
        return f"vault schema {schema} is older than {JOB_MIN_SCHEMA}; run juno migrate"
    return None


def job_refusal(cfg: config.Config, env: Mapping[str, str] | None = None) -> str | None:
    env = cfg.env if env is None else env
    off = enabled_refusal(cfg)
    if off:
        return off
    if env.get("JUNO_DISABLE") == "1":
        return "JUNO_DISABLE is set"
    if any(env.get(name) for name in cardjob.SESSION_VARS):
        return "inside a Claude session"
    if envmod.agent_context(env):
        return "agent context"
    return None


def refusal_line(reason: str) -> str:
    return f"Juno: refused: {reason}"
