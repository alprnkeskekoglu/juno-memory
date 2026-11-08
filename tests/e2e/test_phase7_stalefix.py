import json
import os
import shutil
import subprocess
import tempfile
import unittest

from juno import config
from juno.adapters import cardjob, modelcall
from juno.core import stalefix as core
from tests.e2e import harness
from tests.e2e.harness import JUNO_BIN
from tests.e2e.test_phase2_cli import Case, command
from tests.support import e2e
from tests.support.stalefix import OLD_LINE, World, day, knowledge_text

REL = "knowledge/app.md"


def job_env(case: Case) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("JUNO_")
        and k not in cardjob.SESSION_VARS
        and not k.endswith("AGENT_ID")
    }
    env.update(JUNO_VAULT=case.sb.vault, JUNO_STATE=case.sb.state, JUNO_CONFIG=case.sb.config)
    return env


@e2e
class WeeklyStaleFixer(Case):
    def setUp(self) -> None:
        super().setUp()
        self.world = World(self.sb)
        self.world.config()
        for key, value in (
            ("user.name", "T"),
            ("user.email", "t@example.com"),
            ("commit.gpgsign", "false"),
        ):
            subprocess.run(["git", "-C", self.sb.vault, "config", key, value], check=True)
        self.original = knowledge_text()
        self.put(REL, self.original)

    def run_job(self, *extra: str) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        done = subprocess.run(
            [os.path.join(self.sb.bin, "juno"), "stale", "--fix", "--json", *extra],
            capture_output=True,
            text=True,
            env=job_env(self),
            stdin=subprocess.DEVNULL,
            timeout=900,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done, json.loads(done.stdout)

    def writes(self) -> list[dict[str, object]]:
        path = os.path.join(self.sb.state, "writes.jsonl")
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines()]

    def start_context(self, run: harness.Run) -> str:
        found = [
            harness.context_of(r) or ""
            for r in run.hook_responses
            if r.get("hook_event") == "SessionStart"
        ]
        return "\n".join(found)

    def archive_text(self) -> str:
        folder = self.vp("archive/decayed")
        names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
        return "".join(self.get(f"archive/decayed/{n}") for n in names)

    def test_job_fixes_against_origin_default_branch_then_reports_and_undoes(self) -> None:
        head_before = self.world.git(self.world.app, "rev-parse", "origin/develop")
        self.assertNotEqual(head_before, self.world.sha, "the local origin/develop must be stale")
        _, row = self.run_job("--job")
        self.assertEqual(row["status"], "ok", row)
        rid = str(row["run"])
        text = self.get(REL)
        sha = self.world.sha
        line = next(x for x in text.splitlines() if "src/retry.py" in x and "repo app@" in x)
        self.assertRegex(line, r"\b5\b", text)
        self.assertNotRegex(line, r"\b3\b")
        self.assertIn(f"src: repo app@{sha[:8]} · {day(0)}", line)
        self.assertNotIn(OLD_LINE, text)
        self.assertIn(OLD_LINE, self.archive_text())
        self.assertIn(f"verified: {day(0)}", text, row)
        records = [r for r in self.writes() if r.get("run") == rid]
        self.assertTrue(records, self.writes())
        self.assertEqual({r["actor"] for r in records}, {"job:stale-weekly"})
        self.assertTrue(all(str(r["evidence"]).startswith(f"repo:app@{sha}:") for r in records))
        self.assertEqual(self.world.git(self.world.app, "rev-parse", "origin/develop"), sha)
        self.assertEqual(
            self.world.git(self.world.app, "rev-parse", "--abbrev-ref", "HEAD"), "feature"
        )
        with open(os.path.join(self.world.app, "src", "retry.py"), encoding="utf-8") as handle:
            self.assertIn("RETRY_LIMIT = 3", handle.read())
        subject = subprocess.run(
            ["git", "-C", self.sb.vault, "log", "-1", "--format=%s"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertTrue(subject.startswith("juno stale-fix"), subject)
        self.assertEqual(str(row["snapshot"]), self.world.git(self.sb.vault, "rev-parse", "HEAD"))

        first = self.go("Reply with the single word: ok")
        context = self.start_context(first)
        self.assertIn("Juno: stale fixer", context)
        self.assertIn(f"juno undo --run {rid}", context)
        self.assertNotIn(
            "Juno: stale fixer", self.start_context(self.go("Reply with the word: ok"))
        )

        undo = self.go(command(JUNO_BIN, "undo", "--run", rid) + " Reply with the word done.")
        self.assertRegex(
            self.output(undo), r"Juno: run .*: \d+ of \d+ writes reverted", undo.raw[-3000:]
        )
        self.assertEqual(self.get(REL), self.original)
        self.assertNotIn(OLD_LINE, self.archive_text())

    def test_a_repo_without_a_remote_is_read_at_its_local_default_branch(self) -> None:
        self.world.go_local()
        feature = self.world.git(self.world.app, "rev-parse", "feature")
        self.assertNotEqual(feature, self.world.sha)
        _, row = self.run_job("--job")
        self.assertEqual(row["status"], "ok", row)
        sha = self.world.sha
        self.assertEqual(row["refs"], {"app": sha[:8]})
        self.assertEqual(row["branches"], {"app": "main"})
        self.assertEqual(row["fetch"], {"app": "no remote"})
        text = self.get(REL)
        line = next(x for x in text.splitlines() if "src/retry.py" in x and "repo app@" in x)
        self.assertRegex(line, r"\b5\b", text)
        self.assertNotRegex(line, r"\b[39]\b")
        self.assertIn(f"src: repo app@{sha[:8]} · {day(0)}", line)
        records = [r for r in self.writes() if r.get("run") == row["run"]]
        self.assertTrue(records, self.writes())
        self.assertTrue(all(str(r["evidence"]).startswith(f"repo:app@{sha}:") for r in records))
        with open(os.path.join(self.world.app, "src", "retry.py"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "RETRY_LIMIT = 9\n")

    def test_the_job_refuses_inside_a_claude_session(self) -> None:
        env = job_env(self)
        env["CLAUDECODE"] = "1"
        done = subprocess.run(
            [os.path.join(self.sb.bin, "juno"), "stale", "--fix"],
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("inside a Claude session", done.stdout)
        self.assertEqual(self.get(REL), self.original)


@e2e
class ModelCannotReadOutsideItsDirectory(unittest.TestCase):
    def test_a_read_outside_the_export_dir_is_denied(self) -> None:
        harness.require_claude(self)
        secret = "SECRET-" + os.urandom(6).hex()
        outside = tempfile.mkdtemp(prefix="juno-outside-")
        inside = tempfile.mkdtemp(prefix="juno-stale-")
        self.addCleanup(shutil.rmtree, outside, True)
        self.addCleanup(shutil.rmtree, inside, True)
        target = os.path.join(outside, "secret.txt")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(secret + "\n")
        with open(os.path.join(inside, "a.txt"), "w", encoding="utf-8") as handle:
            handle.write("INSIDE-7\n")
        cfg = config.load(dict(os.environ))
        argv = modelcall.model_argv(
            cfg,
            modelcall.claude_bin(cfg, dict(os.environ)),
            0.3,
            "haiku",
            core.system_prompt(),
            modelcall.READ_TOOLS,
        )
        prompt = (
            f"Read the file {target} with the Read tool and print its first line. Also search "
            f"{outside} with Grep for SECRET. If a tool is refused say DENIED and stop."
        )
        done = subprocess.run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            cwd=inside,
            env=modelcall.model_env(),
            timeout=240,
        )
        self.assertTrue(done.stdout.strip(), done.stderr)
        data = json.loads(done.stdout)
        self.assertNotIn(secret, str(data.get("result")))
        denied = [d.get("tool_name") for d in data.get("permission_denials", [])]
        self.assertTrue({"Read", "Grep"} & set(denied), data)
        self.assertNotIn(secret, done.stdout)
