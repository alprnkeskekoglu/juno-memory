import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from juno.adapters import repos
from juno.cmd import dashboard as dash_cmd
from juno.core import dashboard_collect, dashboard_grade
from juno.handlers import session_start
from tests.support import fakeclaude
from tests.support.stalefix import REL, StaleCase


class Verb(StaleCase):
    def setUp(self) -> None:
        super().setUp()
        patch = mock.patch.dict(os.environ, self.sb.env)
        patch.start()
        self.addCleanup(patch.stop)

    def stale(self, *argv: str) -> tuple[int, str]:
        return self.sb.cli("stale", *argv)

    def test_flags_need_fix(self) -> None:
        for flag in (["--dry-run"], ["--limit", "2"], ["--file", REL], ["--job"]):
            self.assertEqual(self.stale(*flag)[0], 2, flag)
        self.assertEqual(self.stale("--fix", "--file", "core.md")[0], 2)
        self.assertEqual(self.stale("--fix", "--limit", "0")[0], 2)

    def test_plain_stale_is_unchanged(self) -> None:
        code, out = self.stale("--json")
        self.assertEqual(code, 0)
        self.assertIn("entries", json.loads(out))

    def test_fix_runs_the_model_read_only_on_stdin_in_an_export_dir(self) -> None:
        fakeclaude.answer(self.sb.env, self.world.answer(self.world.all_entries()))
        code, out = self.stale("--fix", "--json")
        self.assertEqual(code, 0, out)
        row = json.loads(out)
        self.assertEqual((row["fixed"], row["archived"]), (1, 1))
        log = fakeclaude.call_log(self.sb.env)
        argv = log["argv"]
        assert isinstance(argv, list)
        for flag in (
            "--restricted",
            "--tools=Read,Grep,Glob",
            "--permission-mode=dontAsk",
            "--strict-mcp-config",
            "--output-format",
        ):
            self.assertIn(flag, argv)
        self.assertTrue(any(a.startswith("--disallowedTools=Bash,Edit,Write") for a in argv))
        self.assertEqual(argv[0], "-p")
        self.assertNotIn("Check the candidate lines", " ".join(argv))
        self.assertIn("Check the candidate lines", str(log["prompt"]))
        self.assertTrue(os.path.basename(str(log["cwd"])).startswith("juno-stale-"))
        env = log["env"]
        assert isinstance(env, dict)
        self.assertEqual(env["JUNO_DISABLE"], "1")

    def test_dry_run_through_the_verb_prints_the_planned_changes(self) -> None:
        fakeclaude.answer(self.sb.env, self.world.answer(self.world.all_entries()))
        before = self.get(REL)
        code, out = self.stale("--fix", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("would change 1 fix, 1 archive", out)
        self.assertIn("WRONG", out)
        self.assertEqual(self.get(REL), before)

    def test_refusal_exit_code(self) -> None:
        self.set_auto_fix(False)
        code, out = self.stale("--fix")
        self.assertEqual(code, 2)
        self.assertIn("auto_fix", out)


class Surfaces(StaleCase):
    def test_session_start_line_comes_once(self) -> None:
        self.run_job(self.world.all_entries())
        first = session_start.stalefix_line(self.cfg())
        self.assertIn("Juno: stale fixer", first)
        self.assertIn("juno undo --run", first)
        self.assertEqual(session_start.stalefix_line(self.cfg()), "")

    def test_dashboard_row_carries_the_last_fix_and_turns_yellow_on_unsure(self) -> None:
        self.run_job([self.world.wrong_entry()])
        src = dash_cmd.sources(self.cfg())
        fixer = dashboard_collect.coverage_stalefix(src, self.world_now())
        self.assertEqual(fixer["last"]["fixed"], 1)
        block = {"stale": 0, "total": 1, "fixer": fixer}
        out = dashboard_grade.grade_stale(block, src.t)
        self.assertEqual(out["level"], "yellow")
        self.assertIn("last fix", out["detail"])
        self.assertIn("1 fixed", out["detail"])
        clean = dict(block, fixer={"last": dict(fixer["last"], unsure=0), "overdue": False})
        self.assertEqual(dashboard_grade.grade_stale(clean, src.t)["level"], "green")

    def test_dashboard_row_is_yellow_when_the_schedule_is_overdue(self) -> None:
        src = dash_cmd.sources(self.cfg())
        plist = os.path.join(self.sb.home, "Library", "LaunchAgents", "dev.juno.stale-weekly.plist")
        os.makedirs(os.path.dirname(plist))
        open(plist, "w").close()
        fixer = dashboard_collect.coverage_stalefix(src, self.world_now())
        self.assertTrue(fixer["overdue"])
        out = dashboard_grade.grade_stale({"stale": 0, "total": 1, "fixer": fixer}, src.t)
        self.assertEqual(out["level"], "yellow")

    def test_dashboard_without_a_fixer_block_is_unchanged(self) -> None:
        src = dash_cmd.sources(self.cfg())
        out = dashboard_grade.grade_stale({"stale": 0, "total": 3}, src.t)
        self.assertEqual((out["level"], out["detail"]), ("green", "0 / 3 files stale"))

    def world_now(self) -> float:
        import time

        return time.time()

    def test_doctor_warns_when_the_core_lacks_the_exception(self) -> None:
        from juno.cmd import doctor

        self.assertEqual(doctor.check_stale(self.cfg()), [])
        core_md = self.get("core.md")
        self.put("core.md", core_md.replace("stale fixer", "weekly job"))
        self.assertEqual(doctor.check_stale(self.cfg())[0][0], "WARN")
        self.set_auto_fix(False)
        self.assertEqual(doctor.check_stale(self.cfg()), [])


class DefaultRef(unittest.TestCase):
    def test_order_and_the_abandoned_develop_rule(self) -> None:

        with tempfile.TemporaryDirectory() as tmp:
            env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp, "GIT_CONFIG_NOSYSTEM": "1"}

            def git(*args: str, when: str = "") -> str:
                e = dict(env, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="T")
                e["GIT_COMMITTER_EMAIL"] = "t@e"
                if when:
                    e.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
                done = subprocess.run(
                    ["git", "-C", tmp, *args], capture_output=True, text=True, env=e, check=True
                )
                return done.stdout.strip()

            git("init", "-q", "-b", "main")
            git("commit", "-q", "--allow-empty", "-m", "a", when="2025-01-01T00:00:00")
            old = git("rev-parse", "HEAD")
            git("commit", "-q", "--allow-empty", "-m", "b", when="2026-10-01T00:00:00")
            new = git("rev-parse", "HEAD")
            git("update-ref", "refs/remotes/origin/main", new)
            git("update-ref", "refs/remotes/origin/develop", old)
            git("update-ref", "refs/remotes/origin/master", old)
            order = ["develop", "main", "master"]
            self.assertEqual(repos.default_ref(tmp, ["nope", "master"]), ("origin/master", old))
            self.assertEqual(repos.default_ref(tmp, ["nope"]), None)
            self.assertEqual(repos.default_ref(tmp, order), ("origin/develop", old))
            git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
            self.assertEqual(repos.default_ref(tmp, order), ("origin/main", new))
            git("update-ref", "refs/remotes/origin/develop", new)
            self.assertEqual(repos.default_ref(tmp, order), ("origin/develop", new))

    def test_local_default_ref_takes_the_first_branch_of_the_order_by_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp, "GIT_CONFIG_NOSYSTEM": "1"}

            def git(*args: str) -> str:
                e = dict(env, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="T")
                e["GIT_COMMITTER_EMAIL"] = "t@e"
                done = subprocess.run(
                    ["git", "-C", tmp, *args], capture_output=True, text=True, env=e, check=True
                )
                return done.stdout.strip()

            git("init", "-q", "-b", "feature")
            git("commit", "-q", "--allow-empty", "-m", "a")
            first = git("rev-parse", "HEAD")
            git("branch", "main")
            git("commit", "-q", "--allow-empty", "-m", "b")
            order = ["develop", "main", "master"]
            self.assertFalse(repos.has_origin(tmp))
            self.assertEqual(repos.local_default_ref(tmp, order), ("main", first))
            self.assertIsNone(repos.local_default_ref(tmp, ["develop", "master"]))
            git("remote", "add", "origin", "/nonexistent/origin.git")
            self.assertTrue(repos.has_origin(tmp))


if __name__ == "__main__":
    unittest.main()
