import json
import os
import shutil
import subprocess
import unittest
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from tests.support import REPO, Sandbox

COST = 0.0
HOOK_CAP = 9500
JUNO_BIN = os.path.join(REPO, "bin", "juno")
DEFAULT_MODEL = "haiku"
BUDGET_SUBTYPE = "error_max_budget_usd"


def default_model(env: Mapping[str, str] | None = None) -> str:
    """Reply model for cases that name none: `JUNO_E2E_MODEL`, else haiku."""
    value = (os.environ if env is None else env).get("JUNO_E2E_MODEL", "").strip()
    return value or DEFAULT_MODEL


def budget_for(model: str, env: Mapping[str, str] | None = None) -> float:
    """Per-call cap in USD: `JUNO_E2E_BUDGET`, else 0.5 for haiku and 2.0 for any other model."""
    raw = (os.environ if env is None else env).get("JUNO_E2E_BUDGET", "").strip()
    if raw:
        return float(raw)
    return 0.5 if "haiku" in model.lower() else 2.0


@dataclass
class Run:
    events: list[dict[str, Any]]
    returncode: int
    stderr: str
    raw: str
    result: str = ""
    cost: float = 0.0
    hook_responses: list[dict[str, Any]] = field(default_factory=list)
    budget: float = 0.0
    budget_stop: bool = False


def budget_message(budget: float) -> str:
    return f"claude -p stopped at the budget cap ${budget:g}; set JUNO_E2E_BUDGET"


def require_claude(case: unittest.TestCase) -> None:
    if shutil.which("claude") is None:
        case.skipTest("claude CLI not installed")


def claude_env(sb: Sandbox, interactive: bool) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("JUNO_")}
    env.pop("CLAUDE_CODE_SESSION_ATTENDED", None)
    env.update(
        JUNO_VAULT=sb.vault,
        JUNO_STATE=sb.state,
        JUNO_CONFIG=sb.config,
    )
    if interactive:
        env["JUNO_TEST_INTERACTIVE"] = "1"
    return env


def run_claude(
    prompt: str,
    env: dict[str, str],
    model: str | None = None,
    timeout: int = 240,
    cwd: str | None = None,
    extra: list[str] | None = None,
) -> Run:
    global COST
    model = model or default_model()
    budget = budget_for(model)
    argv = [
        "claude",
        "-p",
        prompt,
        "--plugin-dir",
        REPO,
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-hook-events",
        "--max-budget-usd",
        f"{budget:g}",
        *(extra or []),
    ]
    done = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        cwd=cwd,
    )
    events = []
    for line in done.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    run = Run(events, done.returncode, done.stderr, done.stdout, budget=budget)
    for event in events:
        if event.get("type") == "result":
            run.result = str(event.get("result", ""))
            run.cost = float(event.get("total_cost_usd", 0.0))
        if event.get("subtype") == "hook_response":
            run.hook_responses.append(event)
    run.budget_stop = stopped_on_budget(events, budget)
    if run.budget_stop:
        run.stderr = f"{budget_message(budget)}\n{run.stderr}".rstrip()
    COST += run.cost
    return run


def stopped_on_budget(events: list[dict[str, Any]], budget: float) -> bool:
    """True when the stream's result event is the budget error (or an error at or over the cap)."""
    for event in events:
        if event.get("type") != "result":
            continue
        if event.get("subtype") == BUDGET_SUBTYPE:
            return True
        cost = float(event.get("total_cost_usd", 0.0) or 0.0)
        if event.get("is_error") and cost >= budget:
            return True
    return False


def context_of(response: dict[str, Any]) -> str | None:
    try:
        payload = json.loads(response.get("stdout", ""))
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    output = payload.get("hookSpecificOutput")
    if not isinstance(output, dict):
        return None
    text = output.get("additionalContext")
    return text if isinstance(text, str) else None


def juno_contexts(run: Run, event: str | None = None) -> list[str]:
    found = []
    for response in run.hook_responses:
        if event and response.get("hook_event") != event:
            continue
        text = context_of(response)
        if text is not None and ("Juno" in text or "RULES (follow)" in text):
            found.append(text)
    return found


def juno_hook_failures(run: Run) -> list[dict[str, Any]]:
    bad = []
    for response in run.hook_responses:
        blob = json.dumps(response).lower()
        if "juno" in blob and (
            response.get("exit_code") not in (0, None) or response.get("outcome") == "error"
        ):
            bad.append(response)
    return bad


def init_with_bin(sb: Sandbox, *extra: str) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(
        [JUNO_BIN, "init", *sb.install_args(), *extra],
        capture_output=True,
        text=True,
        env=sb.env,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    if done.returncode == 0:
        pin_claude_dir(sb)
    return done


def pin_claude_dir(sb: Sandbox) -> None:
    """Pin claude_dir to the sandbox."""
    with open(sb.config, "a", encoding="utf-8") as handle:
        handle.write(f'\n[paths]\nclaude_dir = "{os.path.dirname(sb.settings)}"\n')


def shim(sb: Sandbox, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [os.path.join(sb.bin, "juno"), *argv],
        capture_output=True,
        text=True,
        env=sb.env,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
