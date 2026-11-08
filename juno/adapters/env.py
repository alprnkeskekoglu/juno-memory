import os
from collections.abc import Mapping

from juno import paths


def test_override(env: Mapping[str, str]) -> bool:
    if env.get("JUNO_TEST_INTERACTIVE") != "1":
        return False
    return (
        bool(env.get("JUNO_CONFIG"))
        and paths.config_path(env) != paths.default_config_path(env)
        and bool(env.get("JUNO_VAULT"))
        and paths.vault_dir(env) != paths.expand(paths.DEFAULT_VAULT, env)
        and bool(env.get("JUNO_STATE"))
        and paths.state_dir(env) != paths.default_state_dir(env)
    )


def claude_args_interactive(pid: str) -> bool:
    if not pid.isdigit():
        return False
    import subprocess

    try:
        out = subprocess.run(
            ["/bin/ps", "-o", "command=", "-p", pid],
            capture_output=True,
            text=True,
            timeout=1,
        ).stdout
    except OSError, subprocess.SubprocessError:
        return False
    args = out.split()
    if not args or "claude" not in os.path.basename(args[0]):
        return False
    return not any(a in ("-p", "--print") or a.startswith("--print=") for a in args[1:])


def is_interactive(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    if env.get("JUNO_DISABLE") == "1":
        return False
    if test_override(env):
        return True
    attended = env.get("CLAUDE_CODE_SESSION_ATTENDED")
    if attended == "1":
        return True
    if attended == "0":
        return False
    return claude_args_interactive(env.get("CLAUDE_PID", ""))


def agent_context(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return any(key.endswith("AGENT_ID") and value for key, value in env.items())
