import datetime
import json
import os
import re
import subprocess
from typing import Any

from juno import config
from juno.adapters import writelog
from juno.cmd import stalefix as cmd
from tests.support import Sandbox, SandboxCase

OLD_DAYS = 120
VERIFIED_DAYS = 90
RETRY_LINE = "- The retry limit is 3 in `src/retry.py` · src: ticket ABC-1 · 2026-01-01"
OLD_LINE = "- The legacy exporter lives in `src/old.py` and runs nightly"
OK1_LINE = "- Backoff is 2 seconds, set in `src/retry.py`"
OK2_LINE = "- The limit constant is named RETRY_LIMIT in `src/retry.py`"
PLAIN_LINE = "- Deploys go out on Tuesdays"


def day(offset: int) -> str:
    return (datetime.date.today() - datetime.timedelta(days=offset)).isoformat()


def knowledge_text(verified: int = VERIFIED_DAYS, full: bool = True) -> str:
    lines = [RETRY_LINE, OLD_LINE, *([OK1_LINE, OK2_LINE] if full else []), PLAIN_LINE]
    return (
        f"---\ntopic: app\nrepos: [app]\nverified: {day(verified)}\n---\n# app\n\n"
        + "\n".join(lines)
        + "\n"
    )


class World:
    def __init__(self, sb: Sandbox) -> None:
        self.sb = sb
        self.projects = os.path.join(sb.root, "projects")
        self.origin = os.path.join(sb.root, "origin.git")
        self.app = os.path.join(self.projects, "app")
        self.helper = os.path.join(sb.root, "helper")
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": sb.home,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        with open(os.path.join(sb.home, ".gitconfig"), "w", encoding="utf-8") as handle:
            handle.write(
                "[user]\n\tname = T\n\temail = t@example.com\n[commit]\n\tgpgsign = false\n"
            )
        self.build()

    def git(self, directory: str, *args: str, when: str | None = None) -> str:
        env = dict(self.env)
        if when:
            env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        done = subprocess.run(
            ["git", "-C", directory, *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        if done.returncode:
            raise AssertionError(f"git {args}: {done.stderr}")
        return done.stdout.strip()

    def write(self, directory: str, rel: str, text: str | None) -> None:
        path = os.path.join(directory, rel)
        if text is None:
            os.unlink(path)
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def build(self) -> None:
        os.makedirs(self.projects)
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "develop", self.origin],
            check=True,
            env=self.env,
        )
        subprocess.run(["git", "init", "-q", "-b", "develop", self.app], check=True, env=self.env)
        self.git(self.app, "remote", "add", "origin", self.origin)
        self.write(self.app, "src/retry.py", "RETRY_LIMIT = 3\nBACKOFF = 2\n")
        self.write(self.app, "src/old.py", "def export():\n    return 1\n")
        self.git(self.app, "add", "-A")
        old = f"{day(OLD_DAYS)}T12:00:00"
        self.git(self.app, "commit", "-q", "-m", "first", when=old)
        self.git(self.app, "push", "-q", "origin", "develop")
        self.git(self.app, "checkout", "-q", "-b", "feature")
        self.git(self.sb.root, "clone", "-q", self.origin, self.helper)
        self.write(self.helper, "src/retry.py", "RETRY_LIMIT = 5\nBACKOFF = 2\n")
        self.write(self.helper, "src/old.py", None)
        self.git(self.helper, "add", "-A")
        self.git(self.helper, "commit", "-q", "-m", "raise the retry limit to 5, drop old.py")
        self.git(self.helper, "push", "-q", "origin", "develop")
        self.sha = self.git(self.helper, "rev-parse", "HEAD")

    def go_local(self, branch: str = "main") -> None:
        self.git(self.app, "remote", "remove", "origin")
        self.git(self.app, "checkout", "-q", "develop")
        self.write(self.app, "src/retry.py", "RETRY_LIMIT = 5\nBACKOFF = 2\n")
        self.write(self.app, "src/old.py", None)
        self.git(self.app, "add", "-A")
        self.git(self.app, "commit", "-q", "-m", "raise the retry limit to 5, drop old.py")
        self.sha = self.git(self.app, "rev-parse", "HEAD")
        self.git(self.app, "branch", "-m", "develop", branch)
        self.git(self.app, "checkout", "-q", "feature")
        self.write(self.app, "src/retry.py", "RETRY_LIMIT = 9\n")

    def config(self, extra: str = "") -> None:
        if not hasattr(self, "base"):
            with open(self.sb.config, encoding="utf-8") as handle:
                self.base = handle.read()
        with open(self.sb.config, "w", encoding="utf-8") as handle:
            handle.write(
                f'{self.base}\n[repos]\nroots = ["{self.projects}"]\n[stale]\n'
                f"auto_fix = true\n{extra}\n"
            )

    def answer(self, entries: list[dict[str, Any]]) -> str:
        return json.dumps({"lines": entries})

    def evidence(self, path: str = "src/retry.py", line: int = 1, sha: str | None = None) -> str:
        return f"repo:app@{sha or self.sha}:{path}:{line}"

    def wrong_entry(self, **over: Any) -> dict[str, Any]:
        entry = {
            "line": 8,
            "was": RETRY_LINE,
            "verdict": "WRONG",
            "new": "The retry limit is 5 in `src/retry.py`",
            "evidence": [self.evidence()],
            "why": "RETRY_LIMIT is 5 on develop",
        }
        entry.update(over)
        return entry

    def gone_entry(self, **over: Any) -> dict[str, Any]:
        entry = {
            "line": 9,
            "was": OLD_LINE,
            "verdict": "GONE",
            "evidence": [self.evidence("src/old.py", 1)],
            "why": "old.py was deleted",
        }
        entry.update(over)
        return entry

    def ok_entry(self, line: int, was: str) -> dict[str, Any]:
        return {"line": line, "was": was, "verdict": "OK", "evidence": [self.evidence()]}

    def all_entries(self) -> list[dict[str, Any]]:
        return [
            self.wrong_entry(),
            self.gone_entry(),
            self.ok_entry(10, OK1_LINE),
            self.ok_entry(11, OK2_LINE),
        ]


REL = "knowledge/app.md"


class Calls:
    def __init__(self, text: str, cost: float = 0.05) -> None:
        self.text, self.cost = text, cost
        self.prompts: list[str] = []
        self.listings: list[dict[str, str]] = []

    def __call__(
        self, cfg: config.Config, prompt: str, budget: float, workdir: str
    ) -> tuple[str, float]:
        self.prompts.append(prompt)
        seen: dict[str, str] = {}
        for base, _, names in os.walk(workdir):
            for name in names:
                path = os.path.join(base, name)
                with open(path, encoding="utf-8") as handle:
                    seen[os.path.relpath(path, workdir)] = handle.read()
        self.listings.append(seen)
        return self.text, self.cost


class StaleCase(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)
        self.world = World(self.sb)
        self.world.config()
        self.put(REL, knowledge_text())

    def cfg(self) -> config.Config:
        return config.load(dict(self.sb.env))

    def vp(self, rel: str) -> str:
        return os.path.join(self.sb.vault, *rel.split("/"))

    def put(self, rel: str, text: str) -> None:
        os.makedirs(os.path.dirname(self.vp(rel)), exist_ok=True)
        with open(self.vp(rel), "w", encoding="utf-8") as handle:
            handle.write(text)

    def get(self, rel: str) -> str:
        try:
            with open(self.vp(rel), encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return ""

    def run_job(
        self, entries: list[dict[str, Any]], opts: cmd.Options | None = None
    ) -> tuple[cmd.Outcome, Calls]:
        calls = Calls(self.world.answer(entries))
        return cmd.execute(self.cfg(), opts or cmd.Options(), calls), calls

    def set_auto_fix(self, on: bool) -> None:
        with open(self.sb.config, encoding="utf-8") as handle:
            text = handle.read()
        flipped = re.sub(r"auto_fix = \w+", f"auto_fix = {str(on).lower()}", text)
        with open(self.sb.config, "w", encoding="utf-8") as handle:
            handle.write(flipped)

    def vault_head(self) -> str:
        done = subprocess.run(
            ["git", "-C", self.sb.vault, "rev-parse", "-q", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            env=self.world.env,
        )
        return done.stdout.strip()

    def writes(self) -> list[dict[str, object]]:
        return writelog.read(self.cfg())
