import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import subprocess

GIT_TIMEOUT = 1.5


def clean_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def run(args: list[str], directory: str, timeout: float = GIT_TIMEOUT) -> str | None:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", directory, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=clean_env(),
        )
    except OSError, subprocess.SubprocessError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def result(
    args: list[str],
    directory: str,
    timeout: float = 60.0,
    base: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    import subprocess

    env = clean_env(base)
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        return subprocess.run(
            ["git", "-C", directory, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except OSError, subprocess.SubprocessError:
        return None
