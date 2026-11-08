import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from typing import Any
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PYTHON = sys.executable
SCRUBBED = ("CLAUDE", "JUNO", "XDG_")


def mark(name: str):
    def decorate(obj: Any) -> Any:
        obj.juno_marks = frozenset(getattr(obj, "juno_marks", frozenset()) | {name})
        return obj

    return decorate


def e2e(obj: Any) -> Any:
    return mark("e2e")(obj)


def subproc(obj: Any) -> Any:
    return mark("subproc")(obj)


def timing(obj: Any) -> Any:
    return mark("timing")(obj)


def clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(SCRUBBED)}
    env.update(extra)
    return env


class Sandbox:
    def __init__(self) -> None:
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="juno-test-"))
        self.home = os.path.join(self.root, "home")
        self.vault = os.path.join(self.root, "vault")
        self.state = os.path.join(self.root, "state")
        self.config = os.path.join(self.root, "config", "juno.toml")
        self.settings = os.path.join(self.root, "claude", "settings.json")
        self.bin = os.path.join(self.root, "bin")
        os.makedirs(self.home)
        from tests.support import fakeclaude

        self.env = clean_env(
            HOME=self.home,
            JUNO_VAULT=self.vault,
            JUNO_STATE=self.state,
            JUNO_CONFIG=self.config,
            JUNO_BIN_DIR=self.bin,
            **fakeclaude.install(self.root),
        )

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def install_args(self) -> list[str]:
        return ["--claude-settings", self.settings, "--bin-dir", self.bin]

    def write_settings(self, raw: bytes) -> None:
        os.makedirs(os.path.dirname(self.settings), exist_ok=True)
        with open(self.settings, "wb") as handle:
            handle.write(raw)

    def read_settings(self) -> bytes | None:
        try:
            with open(self.settings, "rb") as handle:
                return handle.read()
        except FileNotFoundError:
            return None

    def cli(self, *argv: str) -> tuple[int, str]:
        from juno import cli

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(list(argv), {"JUNO_PYTHON": PYTHON, **self.env})
        return code, buffer.getvalue()

    def sub(
        self, *argv: str, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        env.update(extra_env or {})
        return subprocess.run(
            [PYTHON, "-I", "-S", os.path.join(REPO, "juno", "cli.py"), *argv],
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )


class SandboxCase(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)


@contextlib.contextmanager
def patched_environ(env: dict[str, str]) -> Iterator[None]:
    with mock.patch.dict(os.environ, env, clear=True):
        yield


def realistic_settings() -> dict[str, Any]:
    return {
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "model": "opus",
        "env": {"EXAMPLE_FLAG": "1"},
        "permissions": {
            "allow": ["Bash(git status:*)", "Bash(ls:*)", "Read(~/projects/**)"],
            "deny": ["Read(./.env)"],
            "additionalDirectories": ["~/projects"],
            "defaultMode": "default",
        },
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": "/usr/local/bin/audit-hook", "timeout": 3}
                    ],
                }
            ],
            "Stop": [{"hooks": [{"type": "command", "command": "echo done"}]}],
        },
        "enabledPlugins": {"example-plugin@example-market": True},
        "extraKnownMarketplaces": {
            "example-market": {"source": {"source": "github", "repo": "example/market"}}
        },
        "statusLine": {"type": "command", "command": "echo status"},
        "notes": "café ✓",
    }


def settings_variants() -> dict[str, bytes]:
    base = realistic_settings()
    two = (json.dumps(base, indent=2, ensure_ascii=False) + "\n").encode()
    four = (json.dumps(base, indent=4, ensure_ascii=False) + "\n").encode()
    tabs = (json.dumps(base, indent="\t", ensure_ascii=False) + "\n").encode()
    no_newline = json.dumps(base, indent=2, ensure_ascii=False).encode()
    compact = json.dumps(base, separators=(",", ":")).encode()
    escaped = json.dumps(base, indent=2).encode()
    already = dict(base, autoMemoryEnabled=False)
    memory_on = dict(base, autoMemoryEnabled=True)
    perms_dupe = dict(base)
    perms_dupe["permissions"] = dict(base["permissions"], allow=["Read(~/x/**)"] * 2)
    return {
        "indent2": two,
        "indent4": four,
        "tabs": tabs,
        "no_trailing_newline": no_newline,
        "compact": compact,
        "ascii_escaped": escaped,
        "memory_already_false": (json.dumps(already, indent=2) + "\n").encode(),
        "memory_on": (json.dumps(memory_on, indent=2) + "\n").encode(),
        "duplicate_allow": (json.dumps(perms_dupe, indent=2) + "\n").encode(),
        "empty_object": b"{}\n",
        "empty_file": b"",
        "whitespace_only": b"  \n\n",
        "no_permissions_key": b'{\n  "model": "haiku"\n}\n',
        "crlf": two.replace(b"\n", b"\r\n"),
    }
