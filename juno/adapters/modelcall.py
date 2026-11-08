import os
import shutil
from typing import Any

from juno import config
from juno.adapters import cardjob
from juno.core import cardfmt


def claude_bin(cfg: config.Config, env: dict[str, str]) -> str:
    named = cfg.env.get("JUNO_CARD_CLAUDE") or cfg.get_str("handoff", "claude_bin")
    if named:
        return named
    return shutil.which("claude", path=env.get("PATH")) or "claude"


def model_env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in cardjob.SESSION_VARS and k != "JUNO_CARD_TOKEN"
    }
    env.update(JUNO_DISABLE="1")
    return env


def model_argv(
    cfg: config.Config, binary: str, budget: float, model: str = "", system: str = ""
) -> list[str]:
    return [
        binary,
        "-p",
        "--model",
        model or cfg.get_str("handoff", "model") or "sonnet",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--tools",
        "",
        "--system-prompt",
        system or cardfmt.system_prompt("Juno"),
        "--max-budget-usd",
        f"{budget:.2f}",
        "--output-format",
        "json",
    ]


def kill_group(proc: Any) -> None:
    import signal

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        proc.kill()
    proc.communicate()


def call_model(
    cfg: config.Config, prompt: str, budget: float, model: str = "", system: str = ""
) -> tuple[str, float]:
    import subprocess
    import tempfile

    if budget < cfg.get_float("handoff", "min_run_usd"):
        raise cardfmt.Failed("budget")
    env = model_env()
    timeout = cfg.get_float("handoff", "timeout_seconds")
    cwd = tempfile.mkdtemp(prefix="juno-card-")
    try:
        proc = subprocess.Popen(
            model_argv(cfg, claude_bin(cfg, env), budget, model, system),
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
        try:
            out, _ = proc.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_group(proc)
            raise cardfmt.Failed(f"timeout after {timeout:.0f}s") from None
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
    return cardfmt.parse_output(out, proc.returncode)
