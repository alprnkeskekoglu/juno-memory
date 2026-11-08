import os
import sys
from collections.abc import Mapping

DEFAULT_VAULT = "~/juno-vault"


def home(env: Mapping[str, str]) -> str:
    return env.get("HOME") or os.path.expanduser("~")


def expand(path: str, env: Mapping[str, str]) -> str:
    if path == "~" or path.startswith("~/"):
        path = home(env) + path[1:]
    return os.path.abspath(path)


def default_config_path(env: Mapping[str, str]) -> str:
    xdg = env.get("XDG_CONFIG_HOME", "")
    base = xdg if xdg and os.path.isabs(xdg) else os.path.join(home(env), ".config")
    return os.path.join(base, "juno", "juno.toml")


def config_path(env: Mapping[str, str]) -> str:
    override = env.get("JUNO_CONFIG", "")
    return expand(override, env) if override else default_config_path(env)


def default_state_dir(env: Mapping[str, str]) -> str:
    if sys.platform == "darwin":
        return os.path.join(home(env), "Library", "Application Support", "juno")
    xdg = env.get("XDG_STATE_HOME", "")
    base = xdg if xdg and os.path.isabs(xdg) else os.path.join(home(env), ".local", "state")
    return os.path.join(base, "juno")


def vault_dir(env: Mapping[str, str], configured: str = "") -> str:
    return expand(env.get("JUNO_VAULT") or configured or DEFAULT_VAULT, env)


def state_dir(env: Mapping[str, str], configured: str = "") -> str:
    chosen = env.get("JUNO_STATE") or configured
    return expand(chosen, env) if chosen else default_state_dir(env)


def claude_dir(env: Mapping[str, str], configured: str = "") -> str:
    return expand(configured or "~/.claude", env)


def plugin_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def template_dir() -> str:
    return os.path.join(plugin_root(), "vault-template")
