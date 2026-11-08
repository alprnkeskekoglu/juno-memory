import contextlib
import io
import json
import os
import unittest
from typing import Any

from juno import config
from juno.adapters import checks, ledgerio
from juno.adapters import stalerun as runs
from juno.cmd import stalefix as cmd
from juno.cmd import write
from juno.core import stalefix as core
from juno.core.errors import Refused
from tests.support.stalefix import (
    OLD_LINE,
    PLAIN_LINE,
    REL,
    RETRY_LINE,
    Calls,
    StaleCase,
    day,
    knowledge_text,
)


class FullRun(StaleCase):
    def test_fixes_archives_verifies_and_snapshots_against_the_pinned_sha(self) -> None:
        out, calls = self.run_job(self.world.all_entries())
        row = out.row
        self.assertEqual(row["status"], "ok", row)
        self.assertEqual((row["fixed"], row["archived"], row["verified"]), (1, 1, 1))
        text = self.get(REL)
        sha8 = self.world.sha[:8]
        self.assertIn(
            f"- The retry limit is 5 in `src/retry.py` · src: repo app@{sha8} · {day(0)}", text
        )
        self.assertNotIn(OLD_LINE, text)
        self.assertIn(PLAIN_LINE, text)
        self.assertIn(f"verified: {day(0)}", text)
        archive = [n for n in os.listdir(self.vp("archive/decayed")) if n.startswith("forgotten-")]
        self.assertIn(OLD_LINE, self.get(f"archive/decayed/{archive[0]}"))
        shown = calls.listings[0]
        self.assertEqual(
            shown[os.path.join("app", "src", "retry.py")], "RETRY_LIMIT = 5\nBACKOFF = 2\n"
        )
        self.assertNotIn(os.path.join("app", "src", "old.py"), shown)
        self.assertEqual(row["refs"], {"app": sha8})
        self.assertTrue(self.vault_head())
        self.assertRegex(str(row["snapshot"]), r"^[0-9a-f]{40}$")

    def test_every_write_is_logged_as_the_job_and_linked_to_the_run(self) -> None:
        out, _ = self.run_job(self.world.all_entries())
        records = [r for r in self.writes() if r.get("run") == out.row["run"]]
        self.assertEqual({r["actor"] for r in records}, {"job:stale-weekly"})
        self.assertEqual(
            {r["verb"] for r in records}, {"fix", "forget", "forget-archive", "verify"}
        )
        self.assertTrue(all(str(r["sid"]).startswith("job-stale-weekly-") for r in records))
        self.assertTrue(
            all(r["evidence"].startswith("repo:app@" + self.world.sha) for r in records)
        )

    def test_the_working_tree_branch_is_never_read(self) -> None:
        branch = self.world.git(self.world.app, "rev-parse", "--abbrev-ref", "HEAD")
        self.assertEqual(branch, "feature")
        _, calls = self.run_job([self.world.wrong_entry()])
        self.assertIn("RETRY_LIMIT = 5", calls.listings[0][os.path.join("app", "src", "retry.py")])
        with open(os.path.join(self.world.app, "src", "retry.py"), encoding="utf-8") as handle:
            self.assertIn("RETRY_LIMIT = 3", handle.read())

    def test_prompt_names_the_pinned_commit_and_fences_the_note_lines(self) -> None:
        _, calls = self.run_job([])
        prompt = calls.prompts[0]
        self.assertIn(f"app @ {self.world.sha}", prompt)
        self.assertRegex(prompt, r"<data-[0-9a-f]{12}>")
        self.assertIn(json.dumps(RETRY_LINE, ensure_ascii=False), prompt)
        self.assertIn("`src/old.py` in app: ABSENT at this commit", prompt)

    def test_undo_run_reverts_the_whole_run_newest_first(self) -> None:
        before = self.get(REL)
        out, _ = self.run_job(self.world.all_entries())
        self.assertNotEqual(self.get(REL), before)
        env = dict(
            self.sb.env,
            JUNO_TEST_INTERACTIVE="1",
            CLAUDE_CODE_SESSION_ID="57fb7685-9be8-4bc7-a435-47aa50ccd35f",
        )
        ledgerio.append(
            self.cfg(),
            env["CLAUDE_CODE_SESSION_ID"],
            {"ev": "prompt", "pid": "pid00001", "part": 0, "text": "hello there, a typed prompt"},
        )
        from juno import cli

        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["undo", "--run", str(out.row["run"])], env)
        self.assertEqual(code, 0)
        self.assertEqual(self.get(REL), before)
        self.assertNotIn(
            OLD_LINE,
            "".join(
                self.get(f"archive/decayed/{n}") for n in os.listdir(self.vp("archive/decayed"))
            ),
        )


class NoRemote(StaleCase):
    def setUp(self) -> None:
        super().setUp()
        self.world.go_local()

    def test_a_repo_without_origin_is_read_at_its_local_default_branch_commit(self) -> None:
        out, calls = self.run_job(self.world.all_entries())
        row = out.row
        self.assertEqual(row["status"], "ok", row)
        self.assertEqual((row["fixed"], row["archived"]), (1, 1))
        self.assertEqual(row["refs"], {"app": self.world.sha[:8]})
        self.assertEqual(row["branches"], {"app": "main"})
        self.assertEqual(row["fetch"], {"app": "no remote"})
        self.assertEqual(
            calls.listings[0][os.path.join("app", "src", "retry.py")],
            "RETRY_LIMIT = 5\nBACKOFF = 2\n",
        )
        with open(os.path.join(self.world.app, "src", "retry.py"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "RETRY_LIMIT = 9\n")
        self.assertTrue(
            all(r["evidence"].startswith("repo:app@" + self.world.sha) for r in self.writes())
        )

    def test_no_branch_of_the_order_means_no_default_branch(self) -> None:
        self.world.git(self.world.app, "branch", "-m", "main", "trunk")
        out, calls = self.run_job(self.world.all_entries())
        self.assertEqual(out.row["fetch"], {"app": "no default branch"})
        self.assertEqual(out.row["files_checked"], 0)
        self.assertEqual(calls.prompts, [])

    def test_an_origin_whose_fetch_fails_still_skips_the_repo(self) -> None:
        self.world.git(self.world.app, "remote", "add", "origin", "/nonexistent/origin.git")
        out, calls = self.run_job(self.world.all_entries())
        self.assertEqual(out.row["fetch"], {"app": "no origin default branch"})
        self.assertEqual(out.row["refs"], {})
        self.assertEqual(out.row["files_checked"], 0)
        self.assertEqual(calls.prompts, [])


class DryRun(StaleCase):
    def test_writes_nothing_but_the_run_row(self) -> None:
        before = self.get(REL)
        head = self.vault_head()
        out, _ = self.run_job(self.world.all_entries(), cmd.Options(dry=True))
        self.assertEqual(out.row["status"], "dry-run")
        self.assertEqual((out.row["would_fix"], out.row["would_archive"]), (1, 1))
        self.assertEqual((out.row["fixed"], out.row["archived"]), (0, 0))
        self.assertEqual(self.get(REL), before)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.vault_head(), head)
        self.assertEqual(runs.read_runs(self.cfg())[-1]["status"], "dry-run")
        self.assertEqual(len(out.notes), 2)

    def test_runs_without_auto_fix_but_not_in_an_agent(self) -> None:
        self.world.config()
        self.set_auto_fix(False)
        out, _ = self.run_job([], cmd.Options(dry=True))
        self.assertEqual(out.row["status"], "dry-run")
        self.sb.env["CLAUDE_CODE_AGENT_ID"] = "a"
        out, _ = self.run_job([], cmd.Options(dry=True))
        self.assertEqual(out.row["status"], "refused")


class Validation(StaleCase):
    def written(self, out: cmd.Outcome) -> int:
        return int(out.row["fixed"]) + int(out.row["archived"])

    def reasons(self, out: cmd.Outcome) -> list[str]:
        return [str(r["reason"]) for r in out.row["rejected"]]

    def rejected(self, entry: dict[str, Any], needle: str) -> None:
        before = self.get(REL)
        out, _ = self.run_job([entry])
        self.assertEqual(self.written(out), 0, out.row)
        self.assertTrue(any(needle in r for r in self.reasons(out)), self.reasons(out))
        self.assertEqual(self.get(REL), before)

    def test_was_must_equal_the_line(self) -> None:
        self.rejected(self.world.wrong_entry(was=RETRY_LINE + " "), "was does not match")

    def test_enum_and_keys(self) -> None:
        self.rejected(self.world.wrong_entry(verdict="MAYBE"), "enum")
        self.rejected(self.world.wrong_entry(extra=1), "unknown or missing keys")

    def test_evidence_must_exist_and_be_well_formed(self) -> None:
        self.rejected(self.world.wrong_entry(evidence=[]), "evidence is missing")
        self.rejected(self.world.wrong_entry(evidence=["src/retry.py:1"]), "malformed")

    def test_evidence_must_resolve_at_the_pinned_commit(self) -> None:
        w = self.world
        self.rejected(w.wrong_entry(evidence=[w.evidence(sha="a" * 40)]), "no evidence resolves")
        self.rejected(w.wrong_entry(evidence=[w.evidence(line=99)]), "no evidence resolves")
        self.rejected(w.wrong_entry(evidence=[w.evidence("src/none.py")]), "no evidence resolves")
        self.rejected(
            w.wrong_entry(evidence=["repo:other@" + w.sha + ":src/retry.py:1"]), "no evidence"
        )
        self.rejected(w.wrong_entry(evidence=[w.evidence("../x.py")]), "no evidence")

    def test_new_text_is_checked(self) -> None:
        self.rejected(self.world.wrong_entry(new="line one\nline two"), "one line")
        self.rejected(self.world.wrong_entry(new="x" * 900), "longer than")
        self.rejected(self.world.wrong_entry(new="Completely other words qqqq zzzz"), "rewrite")
        self.rejected(
            self.world.wrong_entry(new="The retry limit is 3 in `src/retry.py`"), "equals"
        )
        key = "AKIA" + "ABCDEFGHIJKLMNOP"
        self.rejected(self.world.wrong_entry(new=f"The retry limit is 5, key {key}"), "private")

    def test_gone_only_when_the_path_is_absent(self) -> None:
        line = "- The retry limit is 3 in `src/retry.py` and it is gone"
        self.put(REL, knowledge_text().replace(RETRY_LINE, line))
        entry = self.world.gone_entry(line=8, was=line, evidence=[self.world.evidence()])
        self.rejected(entry, "still exists")

    def test_unsure_is_never_written(self) -> None:
        out, _ = self.run_job(
            [
                {"line": 8, "was": RETRY_LINE, "verdict": "UNSURE", "why": "cannot tell"},
                {"line": 9, "was": OLD_LINE, "verdict": "UNSURE"},
            ]
        )
        self.assertEqual((out.row["fixed"], out.row["archived"], out.row["verified"]), (0, 0, 0))
        self.assertEqual(out.row["unsure"], 4)
        self.assertNotIn("verified: " + day(0), self.get(REL))

    def test_more_than_half_changed_writes_nothing(self) -> None:
        self.put(REL, knowledge_text(full=False))
        out, _ = self.run_job([self.world.wrong_entry(), self.world.gone_entry()])
        self.assertEqual((out.row["fixed"], out.row["archived"]), (0, 0))
        self.assertIn({"file": REL, "reason": "too many changes"}, out.row["skipped"])

    def test_the_per_file_cap_writes_nothing(self) -> None:
        self.world.config("max_changes_per_file = 1")
        out, _ = self.run_job(self.world.all_entries())
        self.assertEqual((out.row["fixed"], out.row["archived"]), (0, 0))
        self.assertIn({"file": REL, "reason": "too many changes"}, out.row["skipped"])

    def test_a_file_edited_during_the_model_call_is_skipped(self) -> None:
        def move(cfg: config.Config, prompt: str, budget: float, workdir: str) -> tuple[str, float]:
            self.put(REL, self.get(REL).replace(RETRY_LINE, RETRY_LINE + " (edited)"))
            return self.world.answer([self.world.wrong_entry(was=RETRY_LINE)]), 0.05

        out = cmd.execute(self.cfg(), cmd.Options(), move)
        self.assertEqual(self.written(out), 0)
        self.assertIn({"file": REL, "reason": "changed"}, out.row["skipped"])


class Selection(StaleCase):
    def test_fresh_file_with_no_drift_and_files_without_anchors_are_skipped(self) -> None:
        self.put(REL, knowledge_text(verified=0))
        self.put("knowledge/plain.md", "---\nrepos: [app]\nverified: 2020-01-01\n---\n- No paths\n")
        self.put("projects/priv.md", "---\nrepo: app\nvisibility: private\n---\n- `src/a.py` x\n")
        out, calls = self.run_job([])
        reasons = {s["file"]: s["reason"] for s in out.row["skipped"]}
        self.assertEqual(reasons["knowledge/plain.md"], "no code anchor")
        self.assertEqual(reasons["projects/priv.md"], "private")
        self.assertEqual(calls.prompts, [])

    def test_repo_missing_and_limit(self) -> None:
        self.put(
            "knowledge/ghost.md", "---\nrepos: [ghost]\nverified: 2020-01-01\n---\n- `src/a.py` x\n"
        )
        out, calls = self.run_job([], cmd.Options(dry=True, limit=1))
        reasons = {s["file"]: s["reason"] for s in out.row["skipped"]}
        self.assertEqual(reasons["knowledge/ghost.md"], "repo missing")
        self.assertEqual(len(calls.prompts), 1)

    def test_budget_stops_the_run(self) -> None:
        self.put("knowledge/two.md", knowledge_text().replace("# app", "# two"))
        self.world.config("run_budget_usd = 0.06")
        out, calls = self.run_job([])
        self.assertEqual(out.row["status"], "budget")
        self.assertEqual(len(calls.prompts), 1)

    def test_max_usd_option_caps_the_run(self) -> None:
        out, calls = self.run_job([], cmd.Options(dry=True, max_usd=0.01))
        self.assertEqual(calls.prompts, [])
        self.assertEqual(out.row["files_checked"], 0)

    def test_model_failure_twice_fails_the_run(self) -> None:
        self.put("knowledge/two.md", knowledge_text())
        out = cmd.execute(self.cfg(), cmd.Options(), Calls("not json"))
        self.assertEqual(out.row["status"], "failed")
        self.assertEqual(out.row["files_checked"], 0)


class JobIdentity(StaleCase):
    def refusal(self, **env: str) -> str | None:
        cfg = config.load(dict(self.sb.env, **env))
        return checks.job_refusal(cfg)

    def test_accepts_only_the_unattended_job(self) -> None:
        self.assertIsNone(self.refusal())
        seg = write.guard_job(self.cfg(), "stale-weekly", "r1")
        self.assertEqual(seg.sid, "job-stale-weekly-r1")
        with self.assertRaises(Refused):
            write.guard_job(self.cfg(), "other", "r1")

    def test_refuses_inside_claude_and_agents(self) -> None:
        for name in (
            "CLAUDECODE",
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_AGENT_ID",
            "JUNO_DISABLE",
        ):
            self.assertIsNotNone(self.refusal(**{name: "1"}), name)

    def test_refuses_when_off_or_below_schema_two(self) -> None:
        self.set_auto_fix(False)
        self.assertIn("auto_fix", self.refusal() or "")
        self.set_auto_fix(True)
        with open(self.vp("juno-brain.toml"), "w", encoding="utf-8") as handle:
            handle.write("schema_version = 1\n")
        self.assertIn("migrate", self.refusal() or "")

    def test_a_real_run_refuses_in_a_claude_session(self) -> None:
        cfg = config.load(dict(self.sb.env, CLAUDECODE="1"))
        out = cmd.execute(cfg, cmd.Options(), Calls(""))
        self.assertEqual(out.row["status"], "refused")
        self.assertEqual(self.writes(), [])

    def test_the_interactive_guard_refuses_a_job_sid(self) -> None:
        env = dict(
            self.sb.env,
            JUNO_TEST_INTERACTIVE="1",
            CLAUDE_CODE_SESSION_ID="job-stale-weekly-r1",
        )
        ledgerio.append(config.load(env), "job-stale-weekly-r1", {"ev": "prompt", "pid": "p1"})
        self.assertIn("job identity", checks.write_refusal(config.load(env)) or "")


class Scheduling(StaleCase):
    def test_weekly_marker_and_busy_deferral(self) -> None:
        opts = cmd.Options(job=True)
        ledgerio.append(self.cfg(), "s-live", {"ev": "prompt", "pid": "p1"})
        out, calls = self.run_job([], opts)
        self.assertEqual(out.row["status"], "deferred")
        self.assertEqual(calls.prompts, [])
        os.utime(ledgerio.ledger_path(self.cfg(), "s-live") or "", (1_000_000_000, 1_000_000_000))
        out, _ = self.run_job([], opts)
        self.assertEqual(out.row["status"], "ok")
        out, calls = self.run_job([], opts)
        self.assertEqual(out.row["status"], "noop")
        self.assertEqual(calls.prompts, [])
        self.assertEqual([r["status"] for r in runs.read_runs(self.cfg())], ["deferred", "ok"])


class Notice(StaleCase):
    def test_line_is_shown_once_per_run(self) -> None:
        self.assertEqual(runs.notice(self.cfg()), "")
        out, _ = self.run_job([self.world.wrong_entry()])
        line = runs.notice(self.cfg())
        self.assertEqual(
            line,
            f"Juno: stale fixer {day(0)}: 1 lines fixed, 0 archived, 3 unsure "
            f"(juno undo --run {out.row['run']}; juno stale for the rest)",
        )
        self.assertEqual(runs.notice(self.cfg()), "")

    def test_a_failed_run_prints_its_reason_once(self) -> None:
        cmd.execute(config.load(dict(self.sb.env, CLAUDECODE="1")), cmd.Options(), Calls(""))
        self.assertIn("refused: inside a Claude session", runs.notice(self.cfg()))
        cmd.execute(config.load(dict(self.sb.env, CLAUDECODE="1")), cmd.Options(), Calls(""))
        self.assertEqual(runs.notice(self.cfg()), "")


class Pure(unittest.TestCase):
    def cands(self) -> dict[int, core.Candidate]:
        return {3: core.Candidate(3, "- x `a/b.py`", ("a/b.py",))}

    def test_parse_answer(self) -> None:
        self.assertEqual(core.parse_answer('```json\n{"lines": []}\n```'), [])
        for bad in ("nope", "[]", '{"lines": 1}', '{"lines": [], "x": 1}'):
            with self.assertRaises(core.Malformed):
                core.parse_answer(bad)

    def test_duplicate_lines_are_all_rejected(self) -> None:
        entry = {"line": 3, "was": "- x `a/b.py`", "verdict": "UNSURE"}
        verdicts, rejects = core.check_entries([entry, dict(entry)], self.cands())
        self.assertEqual((verdicts, [r.reason for r in rejects]), ([], ["duplicate line"] * 2))

    def test_candidate_lines_skip_headings_fences_and_frontmatter(self) -> None:
        lines = [
            "---",
            "k: `a/b.py`",
            "---",
            "# `a/b.py`",
            "```",
            "- `a/b.py`",
            "```",
            "- ok `a/b.py`",
        ]
        found, skipped = core.candidate_lines(lines, ["a/b.py"], 5)
        self.assertEqual([c.line for c in found], [8])
        self.assertEqual(skipped, 1)

    def test_plan_file_limits(self) -> None:
        wrong = core.Verdict(1, "WRONG", "x", (), "")
        ok = core.Verdict(2, "OK", "", (), "")
        self.assertEqual(core.plan_file(4, [wrong, ok], 5, False).writes, [wrong])
        self.assertEqual(core.plan_file(2, [wrong, ok], 5, False).writes, [wrong])
        self.assertEqual(core.plan_file(2, [wrong, wrong], 5, False).reason, "too many changes")
        self.assertEqual(core.plan_file(9, [wrong, ok], 0, False).reason, "too many changes")
        self.assertTrue(core.plan_file(2, [wrong, ok], 5, False).verify)
        self.assertFalse(core.plan_file(2, [wrong, ok], 5, True).verify)
        self.assertFalse(core.plan_file(3, [wrong, ok], 5, False).verify)


if __name__ == "__main__":
    unittest.main()
