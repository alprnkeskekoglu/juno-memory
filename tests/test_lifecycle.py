import json
import os
import subprocess
import time
import unittest

from juno import config
from juno.adapters import cardjob, checks, ledgerio
from tests.support import PYTHON, REPO, timing
from tests.support.hooks import OTHER_SID, SID, HookCase

BODY = {"tool_name": "Edit", "tool_input": {}, "tool_response": {}}


class Base(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.make_repo("lc")

    def edit(self, n: int = 1, **extra: object) -> None:
        for _ in range(n):
            path = os.path.join(self.repo, "a.txt")
            self.hook(
                "post_tool_use",
                self.payload(
                    "PostToolUse",
                    tool_name="Edit",
                    tool_input={"file_path": path},
                    tool_response={},
                    **extra,
                ),
            )

    def cfg(self) -> config.Config:
        return config.load(dict(self.env))

    def stop(self, **extra: object) -> dict[str, object] | None:
        return self.hook("stop", self.payload("Stop", last_assistant_message="done", **extra))


class StopLogic(Base):
    def test_message_is_stored_redacted_and_capped(self) -> None:
        self.set_config("[caps]\nlast_message_chars = 100\n")
        self.hook(
            "stop", self.payload("Stop", last_assistant_message="ghp_" + "a" * 36 + " " + "x" * 500)
        )
        msg = [e for e in self.ledger() if e["ev"] == "stop"][0]["msg"]
        self.assertIn("[redacted:github]", msg)
        self.assertLessEqual(len(msg), 120)

    def test_end_mode_never_reminds(self) -> None:
        self.edit()
        self.assertIsNone(self.stop())

    def test_status_file_is_written_for_the_session(self) -> None:
        self.edit()
        self.stop()
        for rel in ("status.json", f"status/{SID}.json"):
            with open(os.path.join(self.sb.state, rel), encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertEqual((data["v"], data["segment_id"], data["card_dirty"]), (1, SID, True))

    def test_subagent_stop_is_ignored(self) -> None:
        self.assertIsNone(self.stop(agent_id="a1"))
        self.assertEqual(self.ledger(), [])


class ReminderMode(Base):
    def setUp(self) -> None:
        super().setUp()
        self.set_config('[handoff]\nmode = "reminder"\n')

    def test_dirty_lifecycle_and_card_clear(self) -> None:
        self.assertIsNone(self.stop())
        self.edit()
        text = self.context(self.stop())
        self.assertIn("handoff card for segment 57fb7685 is due", text)
        self.assertIn("Card path:", text)
        self.assertIn("`juno card ", text)
        self.assertNotIn("with Edit", text)
        self.assertNotIn("with Write", text)
        self.assertIsNone(self.stop())
        self.hook(
            "post_tool_use",
            self.payload(
                "PostToolUse",
                tool_name="Bash",
                tool_input={"command": "git commit -m x"},
                tool_response={"stdout": ""},
            ),
        )

    def test_guards_skip_the_reminder(self) -> None:
        self.edit()
        self.assertIsNone(self.stop(stop_hook_active=True))
        self.assertIsNone(self.stop(background_tasks=[{"id": 1}]))

    def test_nosave_and_private_skip(self) -> None:
        self.edit()
        self.hook("user_prompt_submit", self.payload("UserPromptSubmit", prompt="stop [nosave]"))
        self.assertIsNone(self.stop())

    def test_spacing_between_reminders(self) -> None:
        self.edit()
        self.assertTrue(self.context(self.stop()))
        for i in range(2):
            self.hook("user_prompt_submit", self.payload("UserPromptSubmit", prompt=f"turn {i}"))
            self.edit(40)
            self.assertIsNone(self.stop())
        self.hook("user_prompt_submit", self.payload("UserPromptSubmit", prompt="turn 3"))
        self.assertTrue(self.context(self.stop()))


class Failure(Base):
    def test_failure_appends_only_a_ledger_event(self) -> None:
        payload = self.payload(
            "PostToolUseFailure",
            tool_name="Bash",
            error="Exit code 1\nsecret stuff ghp_x",
            tool_input={"command": "false"},
        )
        self.assertIsNone(self.hook("post_tool_use_failure", payload))
        events = self.ledger()
        self.assertEqual(
            [(e["ev"], e["tool"], e["class"]) for e in events], [("fail", "Bash", "exit")]
        )
        self.assertNotIn("secret", json.dumps(events))

    def test_error_classes(self) -> None:
        from juno.handlers.post_tool_use_failure import error_class

        self.assertEqual(error_class({"is_interrupt": True}), "interrupt")
        self.assertEqual(error_class({"error": "Request timed out"}), "timeout")
        self.assertEqual(error_class({"error": "Permission denied"}), "denied")
        self.assertEqual(error_class({"error": "boom"}), "other")

    def test_failure_counts_on_the_segment(self) -> None:
        self.hook(
            "post_tool_use_failure", self.payload("PostToolUseFailure", tool_name="Read", error="x")
        )
        self.assertEqual(ledgerio.fold(self.cfg(), SID).fails, 1)


class PreToolUse(Base):
    def test_vault_edit_is_refused_with_a_pointer_and_not_backed_up(self) -> None:
        path = self.write("notes/n.md", "original\n")
        payload = self.payload(
            "PreToolUse",
            tool_name="Edit",
            tool_input={"file_path": path, "old_string": "a", "new_string": "b"},
        )
        out = self.hook("pre_tool_use", payload)
        assert out is not None
        decision = out["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("juno note", decision["permissionDecisionReason"])
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "backups")))

    def test_repo_file_edit_is_neither_backed_up_nor_denied(self) -> None:
        payload = self.payload(
            "PreToolUse",
            tool_name="Write",
            tool_input={"file_path": os.path.join(self.work, "a.txt"), "content": "y"},
        )
        self.assertIsNone(self.hook("pre_tool_use", payload))
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "backups")))

    def test_ask_record_clean_and_prefilled(self) -> None:
        self.hook(
            "pre_tool_use",
            self.payload(
                "PreToolUse",
                tool_name="AskUserQuestion",
                tool_use_id="t1",
                tool_input={"questions": []},
            ),
        )
        self.hook(
            "pre_tool_use",
            self.payload(
                "PreToolUse",
                tool_name="AskUserQuestion",
                tool_use_id="t2",
                tool_input={"questions": [], "answers": {"q": "a"}},
            ),
        )
        self.assertEqual(
            [(e["tid"], e["clean"]) for e in self.ledger() if e["ev"] == "ask"],
            [("t1", True), ("t2", False)],
        )

    def test_chosen_answers_become_quote_sources_only_after_a_clean_ask(self) -> None:
        question = {
            "question": "Which?",
            "multiSelect": False,
            "options": [
                {"label": "Use tabs", "description": "tabs for indent"},
                {"label": "Spaces"},
            ],
        }
        for tid, extra in (("t1", {}), ("t2", {"answers": {"Which?": "x"}})):
            self.hook(
                "pre_tool_use",
                self.payload(
                    "PreToolUse",
                    tool_name="AskUserQuestion",
                    tool_use_id=tid,
                    tool_input={"questions": [question], **extra},
                ),
            )
            self.hook(
                "post_tool_use",
                self.payload(
                    "PostToolUse",
                    tool_name="AskUserQuestion",
                    tool_use_id=tid,
                    tool_input={"questions": [question]},
                    tool_response={"answers": {"Which?": "Use tabs"}},
                ),
            )
        sources = ledgerio.fold(self.cfg(), SID).quote_sources()
        self.assertEqual([t for k, t in sources if k == "t1"], ["Use tabs"])
        self.assertNotIn("tabs for indent", [t for _, t in sources])
        self.assertNotIn("t2", [k for k, _ in sources])


class SubagentStart(Base):
    def setUp(self) -> None:
        super().setUp()
        self.write(
            "rules/global.md", '- 2026-01-01 · Global subagent rule · src: user abcd1234 · "q"\n'
        )
        self.write(
            "rules/repos/alpha.md", '- 2026-01-01 · Alpha scoped rule · src: user abcd1234 · "q"\n'
        )
        self.write("projects/alpha.md", "## Gotchas\n- g\n")

    def test_block_for_a_repo_cwd_has_scoped_rules_and_pointer(self) -> None:
        repo = self.make_repo("alpha")
        text = self.context(
            self.hook("subagent_start", self.payload("SubagentStart", cwd=repo, agent_id="a1"))
        )
        for expected in (
            "Juno memory applies",
            "Global subagent rule",
            "Alpha scoped rule",
            "Juno pointer",
        ):
            self.assertIn(expected, text)
        self.assertNotIn("g\n", text.split("Juno pointer")[0].split("Alpha scoped rule")[-1])
        self.assertLessEqual(len(text), 3000)

    def test_records_no_ledger_or_pack_events(self) -> None:
        self.hook("subagent_start", self.payload("SubagentStart", agent_id="a1"))
        self.assertEqual(self.ledger(), [])

    def test_main_sessions_last_repo_is_included(self) -> None:
        repo = self.make_repo("alpha")
        self.hook(
            "post_tool_use",
            self.payload(
                "PostToolUse",
                tool_name="Read",
                tool_input={"file_path": os.path.join(repo, "x")},
                tool_response={},
            ),
        )
        text = self.context(
            self.hook("subagent_start", self.payload("SubagentStart", agent_id="a1"))
        )
        self.assertIn("Alpha scoped rule", text)

    def test_nothing_when_parent_is_headless(self) -> None:
        env = {"JUNO_TEST_INTERACTIVE": "0", "CLAUDE_CODE_SESSION_ATTENDED": "0"}
        self.assertIsNone(
            self.hook("subagent_start", self.payload("SubagentStart", agent_id="a1"), env=env)
        )

    def test_nothing_when_vault_is_empty(self) -> None:
        for rel in ("rules/global.md", "rules/repos/alpha.md", "projects/alpha.md"):
            os.unlink(self.vp(rel))
        self.assertIsNone(self.hook("subagent_start", self.payload("SubagentStart", agent_id="a1")))


class WriteChecks(Base):
    def test_refusals_follow_spec_8_1(self) -> None:
        cfg = self.cfg()
        env = dict(self.env, CLAUDE_CODE_SESSION_ID=SID)
        self.assertEqual(checks.write_refusal(cfg, env), "no session ledger")
        ledgerio.append(cfg, SID, {"ev": "start"})
        self.assertIsNone(checks.write_refusal(cfg, env))
        self.assertEqual(
            checks.write_refusal(cfg, dict(env, CLAUDE_CODE_SESSION_ID="")), "no session ledger"
        )
        self.assertEqual(
            checks.write_refusal(cfg, dict(env, CLAUDE_AGENT_ID="a1")), "agent context"
        )
        headless = {k: v for k, v in env.items() if k != "JUNO_TEST_INTERACTIVE"}
        headless["CLAUDE_CODE_SESSION_ATTENDED"] = "0"
        self.assertEqual(checks.write_refusal(cfg, headless), "not an interactive session")
        self.assertEqual(checks.refusal_line("x"), "Juno: refused: x")

    def test_nosave_segment_refuses_writes(self) -> None:
        self.hook(
            "user_prompt_submit", self.payload("UserPromptSubmit", prompt="no memory [nosave]")
        )
        env = dict(self.env, CLAUDE_CODE_SESSION_ID=SID)
        self.assertEqual(checks.write_refusal(self.cfg(), env), "[nosave] is set in this segment")


class PrivateSegments(Base):
    def test_touching_a_private_vault_file_marks_the_segment_private(self) -> None:
        path = self.write("notes/p.md", "---\nvisibility: private\n---\nsecret\n")
        self.hook(
            "post_tool_use",
            self.payload(
                "PostToolUse", tool_name="Read", tool_input={"file_path": path}, tool_response={}
            ),
        )
        self.assertTrue(ledgerio.fold(self.cfg(), SID).private)

    def test_normal_files_do_not(self) -> None:
        path = self.write("notes/n.md", "---\ntitle: x\n---\nbody\n")
        self.hook(
            "post_tool_use",
            self.payload(
                "PostToolUse", tool_name="Read", tool_input={"file_path": path}, tool_response={}
            ),
        )
        self.assertFalse(ledgerio.fold(self.cfg(), SID).private)


class SessionEnd(Base):
    def end(self, **extra: object) -> None:
        self.hook("session_end", self.payload("SessionEnd", reason="other", **extra))

    def last_end(self) -> dict[str, object]:
        return [e for e in self.ledger() if e["ev"] == "end"][-1]

    def wait_for(self, path: str, seconds: float = 10.0) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if os.path.exists(path):
                return True
            time.sleep(0.05)
        return False

    def test_meaningful_dirty_segment_spawns_the_detached_card_run(self) -> None:
        self.edit()
        self.end(transcript_path="/t.jsonl")
        self.assertIsNone(self.last_end()["skip"])
        runs = os.path.join(self.sb.state, "cards", "runs.jsonl")
        self.assertTrue(self.wait_for(runs), "the detached card run did not record itself")
        row = json.loads(open(runs, encoding="utf-8").read().splitlines()[0])
        self.assertEqual((row["sid"], row["status"]), (SID, "failed"))
        self.assertIn("fake claude", row["error"])
        self.assertEqual(row["sections"]["card"][:8], "dropped:")
        job_path = os.path.join(self.sb.state, "cards", f"{SID}.job.json")
        deadline = time.time() + 10
        while (
            time.time() < deadline and cardjob.read_job(self.cfg(), SID).get("status") != "failed"
        ):
            time.sleep(0.05)
        job = json.load(open(job_path, encoding="utf-8"))
        self.assertEqual(
            (job["reason"], job["transcript"], job["max_budget_usd"]), ("other", "/t.jsonl", 0.3)
        )

    def test_skip_reasons(self) -> None:
        self.end()
        self.assertEqual(self.last_end()["skip"], "not meaningful")
        self.edit()
        self.hook("user_prompt_submit", self.payload("UserPromptSubmit", prompt="x [nosave]"))
        self.end()
        self.assertEqual(self.last_end()["skip"], "nosave")
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "cards", f"{SID}.job.json")))

    def test_private_and_clean_card_skip(self) -> None:
        self.edit()
        ledgerio.append(self.cfg(), SID, {"ev": "private", "why": "visibility"})
        self.end()
        self.assertEqual(self.last_end()["skip"], "private")
        ledgerio.append(self.cfg(), OTHER_SID, {"ev": "tool", "edits": [["r", None]]})
        ledgerio.append(self.cfg(), OTHER_SID, {"ev": "card", "path": "handoffs/x.md"})
        self.hook("session_end", self.payload("SessionEnd", session_id=OTHER_SID, reason="other"))
        self.assertEqual(self.last_end_of(OTHER_SID), "card current")

    def last_end_of(self, sid: str) -> object:
        return [e for e in self.ledger(sid) if e["ev"] == "end"][-1]["skip"]

    def test_pending_job_blocks_a_second_run(self) -> None:
        self.edit()
        cardjob.write_job(self.cfg(), SID, {"status": "pending", "t": time.time(), "token": "x"})
        self.end()
        self.assertEqual(self.last_end()["skip"], "running")

    def test_budget_exhausted_skips(self) -> None:
        self.edit()
        cards = os.path.join(self.sb.state, "cards")
        os.makedirs(cards)
        with open(os.path.join(cards, "runs.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"t": time.time(), "cost_usd": 1.99}) + "\n")
        self.end()
        self.assertEqual(self.last_end()["skip"], "budget")
        self.assertEqual(cardjob.run_budget(self.cfg()), 0.01)

    def test_reminder_mode_and_subagents_do_nothing(self) -> None:
        self.edit()
        self.hook("session_end", self.payload("SessionEnd", reason="other", agent_id="a1"))
        self.assertEqual([e for e in self.ledger() if e["ev"] == "end"], [])
        self.set_config('[handoff]\nmode = "reminder"\n')
        self.end()
        self.assertEqual([e for e in self.ledger() if e["ev"] == "end"], [])

    def test_past_the_internal_deadline_records_skip_and_spawns_nothing(self) -> None:
        self.edit()
        cfg = self.cfg()
        payload = self.payload("SessionEnd", reason="other")
        cardjob.on_session_end(cfg, payload, started=time.monotonic() - 1.5)
        self.assertEqual(self.last_end()["skip"], "deadline")
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "cards", f"{SID}.job.json")))

    def test_child_environment(self) -> None:
        cfg = self.cfg()
        os.environ["CLAUDE_CODE_SESSION_ID"] = SID
        try:
            env = cardjob.child_env(cfg, "tok")
        finally:
            del os.environ["CLAUDE_CODE_SESSION_ID"]
        self.assertEqual((env["JUNO_DISABLE"], env["JUNO_CARD_TOKEN"]), ("1", "tok"))
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", env)
        self.assertIn("/usr/bin", env["PATH"])
        self.assertNotIn("~", env["PATH"])

    def test_stub_refuses_a_wrong_token_and_missing_disable(self) -> None:
        cfg = self.cfg()
        cardjob.write_job(cfg, SID, {"status": "pending", "token": "right"})
        script = os.path.join(REPO, "juno", "hooks", "card_run.py")
        for token, disable, code in (("wrong", "1", 1), ("right", "0", 1), ("right", "1", 0)):
            env = dict(self.env, JUNO_CARD_TOKEN=token, JUNO_DISABLE=disable)
            done = subprocess.run(
                [PYTHON, "-I", "-S", script, "run", SID], env=env, capture_output=True, timeout=30
            )
            self.assertEqual(done.returncode, code, (token, disable))
        self.assertEqual(
            subprocess.run(
                [PYTHON, "-I", "-S", script, "bogus"], env=self.env, capture_output=True, timeout=30
            ).returncode,
            2,
        )

    @timing
    def test_session_end_returns_well_inside_the_deadline(self) -> None:
        self.edit()
        started = time.perf_counter()
        done = self.hook_sub("session_end", self.payload("SessionEnd", reason="other"))
        self.assertEqual(done.returncode, 0)
        self.assertLess(time.perf_counter() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
