import contextlib
import importlib
import io
import json
import os
import subprocess
from typing import Any
from unittest import mock

from tests.support import PYTHON, REPO, SandboxCase

SID = "57fb7685-9be8-4bc7-a435-47aa50ccd35f"
OTHER_SID = "e6113328-a090-4f85-980d-917b3105121a"
PREFIXES = ("ABC", "DEF")
ENV_BASE = {"PATH": "/usr/bin:/bin"}


def git(path: str, *args: str) -> None:
    subprocess.run(
        ["git", "-C", path, *args],
        check=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": os.environ.get("HOME", "/")},
    )


class HookCase(SandboxCase):
    TICKETS = True

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)
        self.projects = os.path.join(self.sb.root, "projects")
        self.extra = os.path.join(self.sb.root, "xroot")
        self.work = os.path.join(self.sb.root, "work")
        for directory in (self.projects, self.extra, self.work):
            os.makedirs(directory)
        self.env = dict(
            self.sb.env,
            JUNO_TEST_INTERACTIVE="1",
            JUNO_REPO_ROOTS=os.pathsep.join([self.projects, self.extra]),
            JUNO_CLAUDE_PROJECTS=os.path.join(self.sb.root, "claude-projects"),
        )
        self.config_extra = ""
        if self.TICKETS:
            self.set_config('[jira]\nprefixes = ["ABC", "DEF"]\n')

    def set_config(self, toml: str) -> None:
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write("\n" + toml + "\n")

    def disable_tickets(self) -> None:
        with open(self.sb.config, encoding="utf-8") as handle:
            text = handle.read().replace('prefixes = ["ABC", "DEF"]', "prefixes = []")
        with open(self.sb.config, "w", encoding="utf-8") as handle:
            handle.write(text)

    def vp(self, rel: str) -> str:
        return os.path.join(self.sb.vault, *rel.split("/"))

    def write(self, rel: str, text: str) -> str:
        path = self.vp(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def read(self, rel: str) -> str:
        with open(self.vp(rel), encoding="utf-8") as handle:
            return handle.read()

    def make_repo(self, dirname: str, branch: str = "main", root: str | None = None) -> str:
        path = os.path.join(root or self.projects, dirname)
        os.makedirs(path)
        git(path, "init", "-q", "-b", branch)
        return path

    def payload(self, event: str, **fields: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "session_id": SID,
            "cwd": self.work,
            "prompt_id": "89dd5955-1129-4cae-a085-e150e397d9db",
            "hook_event_name": event,
        }
        base.update(fields)
        return base

    def hook(
        self, event: str, payload: dict[str, Any] | None = None, env: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        module = importlib.import_module(f"juno.hooks.{event}")
        out = io.StringIO()
        data = json.dumps(payload if payload is not None else self.payload(event))
        with (
            mock.patch.dict(os.environ, dict(self.env, **(env or {})), clear=True),
            mock.patch("sys.stdin", io.StringIO(data)),
            contextlib.redirect_stdout(out),
        ):
            code = module.hookcore.run(event, module.handle)
        self.assertEqual(code, 0)
        text = out.getvalue()
        return json.loads(text) if text.strip() else None

    def context(self, out: dict[str, Any] | None) -> str:
        return (out or {}).get("hookSpecificOutput", {}).get("additionalContext", "")

    def ledger(self, sid: str = SID) -> list[dict[str, Any]]:
        path = os.path.join(self.sb.state, "ledgers", sid + ".jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines()]

    def errors(self) -> str:
        path = os.path.join(self.sb.state, "hook-errors.log")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def hook_sub(
        self, event: str, payload: dict[str, Any], env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [PYTHON, "-I", "-S", os.path.join(REPO, "juno", "hooks", f"{event}.py")],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=dict(self.env, **(env or {})),
            timeout=30,
        )
