import json
import os
import stat
import subprocess
import time
import unittest
from unittest import mock

from juno.adapters import cardjob, cardrun, ledgerio, stubs
from juno.core import cardfmt, digest
from tests.support import PYTHON, REPO
from tests.support.cardcase import KEY, PROMPT, SEG8, CardCase, card, with_extract
from tests.support.hooks import OTHER_SID, SID


def transcript_line(kind: str, content: object, **extra: object) -> str:
    return json.dumps({"type": kind, "message": {"content": content}, **extra})


class CardRun(CardCase):
    def test_writes_new_file_and_clears_dirty(self) -> None:
        self.dirty()
        row = self.run_card(card())
        self.assertEqual((row["status"], row["error"]), ("ok", None))
        self.assertEqual(row["files"], [f"handoffs/{KEY}.md"])
        self.assertEqual(self.sections(row)["card"], "ok")
        text = self.handoff()
        self.assertTrue(text.startswith(f"# Handoffs · {KEY}\n"))
        self.assertIn(f"· {KEY} · refund rounding · {SEG8}", text)
        self.assertNotRegex(text, r"## 2026-01-01 00:00")
        self.assertFalse(self.seg().dirty)
        job = cardjob.read_job(self.cfg(), SID)
        self.assertEqual((job["status"], job.get("token")), ("done", None))
        self.assertIn("thread_scan", row)

    def test_model_argv_env_and_cwd(self) -> None:
        self.dirty()
        self.fake(card())
        env = dict(self.env, JUNO_CARD_TOKEN="x", CLAUDE_CODE_SESSION_ID=SID)
        with mock.patch.dict(os.environ, env, clear=True):
            cardrun.run_locked(self.cfg(), SID, self.job())
        seen = self.fake_call()
        argv = seen["argv"]
        assert isinstance(argv, list) and isinstance(seen["env"], dict)
        self.assertEqual(argv[0], "-p")
        for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "0.30")
        self.assertIn("Juno", argv[argv.index("--system-prompt") + 1])
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertEqual(seen["env"]["JUNO_DISABLE"], "1")
        self.assertIsNone(seen["env"]["JUNO_CARD_TOKEN"])
        self.assertIsNone(seen["env"]["CLAUDE_CODE_SESSION_ID"])
        self.assertNotEqual(os.path.realpath(str(seen["cwd"])), os.path.realpath(self.repo))
        prompt = str(seen["prompt"])
        self.assertIn("## YYYY-MM-DD HH:MM · <KEY or -> · <label> · <segment_id[:8]>", prompt)
        self.assertRegex(prompt, r"<data-[0-9a-f]{12}>")
        self.assertIn(PROMPT, prompt)

    def test_digest_uses_typed_prompts_and_transcript(self) -> None:
        self.dirty("fix it, token ghp_" + "a" * 36)
        path = os.path.join(self.sb.root, "t.jsonl")
        lines = [
            transcript_line("assistant", [{"type": "text", "text": "I changed money.py"}]),
            transcript_line(
                "assistant",
                [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/x/money.py"}}],
            ),
            transcript_line(
                "assistant",
                [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "id": "b1",
                        "input": {"command": "git commit -m x"},
                    }
                ],
            ),
            transcript_line(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": "b1",
                        "content": "done https://github.com/o/r/pull/7",
                    }
                ],
            ),
            transcript_line("assistant", [{"type": "text", "text": "side"}], isSidechain=True),
        ]
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        self.run_card(card(), transcript=path)
        prompt = self.calls[0]["prompt"]
        self.assertIn("[redacted:github]", prompt)
        self.assertNotIn("ghp_aaaa", prompt)
        for want in (
            "Files edited:\n- /x/money.py",
            "git commit -m x",
            "PR https://github.com/o/r/pull/7",
        ):
            self.assertIn(want, prompt)
        self.assertIn("I changed money.py", prompt)
        self.assertNotIn("side", prompt)

    def test_replaces_stub_in_place(self) -> None:
        self.dirty()
        cfg = self.cfg()
        self.assertTrue(stubs.write_stub(cfg, self.seg(), time.time()))
        ledgerio.append(cfg, SID, {"ev": "card", "path": f"handoffs/{KEY}.md", "auto": True})
        self.assertIn("auto: true", self.handoff())
        self.redirty()
        row = self.run_card(card())
        self.assertEqual(row["status"], "ok")
        text = self.handoff()
        self.assertEqual(text.count(SEG8), 1)
        self.assertNotIn("auto: true", text)
        self.assertNotIn("stub", text)

    def test_rewrites_own_card_and_appends_to_existing_file(self) -> None:
        self.write(f"handoffs/{KEY}.md", f"# Handoffs · {KEY}\n\n" + card(seg="deadbeef"))
        self.dirty()
        self.run_card(card(body="First version."))
        self.redirty()
        self.run_card(card(body="Second version."))
        text = self.handoff()
        self.assertIn("deadbeef", text)
        self.assertIn("Second version.", text)
        self.assertNotIn("First version.", text)
        self.assertEqual(text.count(SEG8), 1)
        self.assertIn("First version.", self.calls[1]["prompt"])

    def test_invalid_card_writes_nothing_and_leaves_the_segment_dirty(self) -> None:
        self.dirty()
        row = self.run_card("not a card at all")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error"], "invalid: heading")
        self.assertEqual(self.sections(row)["card"], "dropped:invalid: heading")
        self.assertEqual(self.handoff(), "")
        self.assertTrue(self.seg().dirty)
        self.assertEqual(cardjob.read_job(self.cfg(), SID)["status"], "failed")

    def test_model_error_records_cost_and_reason(self) -> None:
        self.dirty()
        failure = {"is_error": True, "subtype": "error_max_budget_usd", "total_cost_usd": 0.3}
        with open(self.env["FAKE_CLAUDE_OUT"], "w", encoding="utf-8") as handle:
            json.dump(failure, handle)
        cardrun.run_locked(self.cfg(), SID, self.job())
        row = cardjob.read_runs(self.cfg())[-1]
        self.assertEqual((row["status"], row["cost_usd"]), ("failed", 0.3))
        self.assertIn("error_max_budget_usd", row["error"])
        for out in ("not json", "[]", json.dumps({"is_error": False})):
            with open(self.env["FAKE_CLAUDE_OUT"], "w", encoding="utf-8") as handle:
                handle.write(out)
            cardrun.run_locked(self.cfg(), SID, self.job())
        errors = [r["error"] for r in cardjob.read_runs(self.cfg())[1:]]
        self.assertIn("model output is not JSON", errors[0])
        self.assertIn("not a JSON object", errors[1])
        self.assertIn("not text", errors[2])

    def test_missing_core_format_fails(self) -> None:
        self.dirty()
        self.write("core.md", "# nothing here\n")
        row = self.run_card(card())
        self.assertEqual(row["status"], "failed")
        self.assertIn("Handoff cards Format", row["error"])
        self.assertEqual(self.calls, [])

    def test_wrong_token_and_missing_disable_are_refused(self) -> None:
        self.dirty()
        cfg = self.cfg()
        cardjob.write_job(cfg, SID, {"status": "pending", "token": "right"})
        self.assertEqual(cardrun.run(cfg, SID, "wrong", self.model(card())), 1)
        self.assertEqual(cardjob.read_runs(cfg), [])
        script = os.path.join(REPO, "juno", "hooks", "card_run.py")
        done = subprocess.run(
            [PYTHON, "-I", "-S", script, "run", SID],
            env=dict(self.env, JUNO_CARD_TOKEN="right", JUNO_DISABLE="0"),
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(done.returncode, 1)

    def test_segment_lock_held_is_refused(self) -> None:
        self.dirty()
        cfg = self.cfg()
        cardjob.write_job(cfg, SID, {"status": "pending", "token": "t"})
        with cardrun.segment_lock(cfg, SID) as first:
            self.assertTrue(first)
            self.assertEqual(cardrun.run(cfg, SID, "t", self.model(card())), 1)
        self.assertEqual(self.calls, [])

    def test_timeout_kills_the_model(self) -> None:
        self.dirty()
        slow = os.path.join(self.sb.root, "slow-claude")
        with open(slow, "w", encoding="utf-8") as handle:
            handle.write(f"#!{PYTHON}\nimport time\ntime.sleep(60)\n")
        os.chmod(slow, os.stat(slow).st_mode | stat.S_IXUSR)
        self.set_config("[handoff]\ntimeout_seconds = 1\n")
        self.env["JUNO_CARD_CLAUDE"] = os.environ["JUNO_CARD_CLAUDE"] = slow
        started = time.time()
        cardrun.run_locked(self.cfg(), SID, self.job())
        row = cardjob.read_runs(self.cfg())[-1]
        self.assertLess(time.time() - started, 20)
        self.assertIn("timeout after 1s", row["error"])

    def test_budget_below_the_minimum_skips_the_model_call(self) -> None:
        self.dirty()
        cardrun.run_locked(self.cfg(), SID, self.job(max_budget_usd=0.01))
        row = cardjob.read_runs(self.cfg())[-1]
        self.assertEqual((row["status"], row["error"], row["cost_usd"]), ("failed", "budget", 0.0))
        self.assertEqual(self.sections(row)["card"], "dropped:budget")
        self.assertEqual(self.fake_call(), {})

    def test_nosave_private_and_current_segments_are_skipped(self) -> None:
        self.dirty()
        self.prompt("later [nosave]")
        row = self.run_card(card())
        self.assertEqual((row["status"], row["error"]), ("skipped", "nosave"))
        self.assertEqual(self.sections(row), dict.fromkeys(self.sections(row), "none"))
        self.assertEqual(self.calls, [])
        self.dirty(sid=OTHER_SID)
        ledgerio.append(self.cfg(), OTHER_SID, {"ev": "card", "path": "handoffs/x.md"})
        self.assertEqual(self.run_card(card(), sid=OTHER_SID)["error"], "card current")

    def test_extract_disabled_asks_for_no_extraction(self) -> None:
        self.set_config("[handoff]\nextract = false\n")
        self.dirty()
        row = self.run_card(with_extract(card(), {"pitfalls": []}))
        self.assertNotIn("JUNO-EXTRACT", self.calls[0]["prompt"])
        self.assertEqual(row["sections"]["pitfalls"], "none")
        self.assertEqual(row["status"], "ok")


class SectionsAreIndependent(CardCase):
    def good_knowledge(self) -> dict[str, object]:
        return {
            "file": "projects/billing-api.md",
            "text": "Money rounding lives in src/money.py",
            "evidence": "src/money.py",
        }

    def test_bad_pitfall_drops_only_itself(self) -> None:
        self.dirty()
        bad = {
            "text": "Never print password=abc123def456ghi789 in logs",
            "evidence": "src/money.py",
        }
        data = {"knowledge": [self.good_knowledge()], "pitfalls": [bad]}
        row = self.run_card(with_extract(card(), data))
        sections = self.sections(row)
        self.assertEqual(sections["card"], "ok")
        self.assertEqual(sections["knowledge"], "ok")
        self.assertEqual(sections["pitfalls"], "dropped:private")
        self.assertIn("Money rounding lives in src/money.py", self.read("projects/billing-api.md"))
        self.assertNotIn("password", self.read("pitfalls.md"))
        self.assertEqual(sections["rules"], "none")

    def test_bad_card_still_writes_anchored_extraction(self) -> None:
        self.dirty()
        data = {"knowledge": [self.good_knowledge()]}
        row = self.run_card(with_extract("no heading here\nLearned: x", data))
        self.assertEqual(row["status"], "failed")
        self.assertTrue(self.sections(row)["card"].startswith("dropped:"))
        self.assertEqual(self.sections(row)["knowledge"], "ok")
        self.assertIn("Money rounding", self.read("projects/billing-api.md"))
        self.assertEqual(self.handoff(), "")

    def test_missing_or_broken_block_is_recorded_per_section(self) -> None:
        self.dirty()
        row = self.run_card(card())
        self.assertEqual(row["extract_error"], "no extract block")
        self.assertEqual(self.sections(row)["card"], "ok")
        self.assertEqual(self.sections(row)["rules"], "dropped:no extract block")
        self.redirty()
        row = self.run_card(with_extract(card(), "{broken"))
        self.assertEqual(row["extract_error"], "extract block is not JSON")
        self.assertEqual(row["status"], "ok")

    def test_wrong_shape_drops_that_kind_only(self) -> None:
        self.dirty()
        data = {"rules": "x", "knowledge": [self.good_knowledge()], "threads": ["x"]}
        sections = self.sections(self.run_card(with_extract(card(), data)))
        self.assertEqual(sections["rules"], "dropped:invalid")
        self.assertEqual(sections["threads"], "dropped:invalid")
        self.assertEqual(sections["knowledge"], "ok")

    def test_vault_lock_busy_keeps_the_card(self) -> None:
        self.dirty()
        from juno.adapters import cardwrite

        data = {"knowledge": [self.good_knowledge()]}
        real = cardwrite.vault_lock
        calls = {"n": 0}

        def flaky(cfg: object, timeout: float = 10.0) -> object:
            calls["n"] += 1
            if calls["n"] == 2:
                raise TimeoutError("busy")
            return real(cfg, timeout)  # type: ignore[arg-type]

        with mock.patch.object(cardwrite, "vault_lock", flaky):
            row = self.run_card(with_extract(card(), data))
        self.assertEqual(self.sections(row)["card"], "ok")
        self.assertEqual(self.sections(row)["knowledge"], "dropped:vault lock busy")


class MiscNaming(CardCase):
    def setUp(self) -> None:
        super().setUp()
        for i in range(5):
            self.prompt(f"research token usage step {i}")
        self.write("handoffs/research.md", "# Handoffs · research\n")

    def card(self, sid: str = SID) -> str:
        return (
            f"## 2026-01-01 00:00 · - · usage research · {sid[:8]}\nRepos: -\nTickets: -\n"
            "Looked at usage.\nLearned: none\n"
        )

    def named(self, slug: str, sid: str = SID) -> dict[str, object]:
        return self.run_card(f"FILE: {slug}\n" + self.card(sid), sid=sid)

    def other(self, tag: str) -> str:
        sid = f"{tag}-{OTHER_SID[len(tag) + 1 :]}"
        for i in range(5):
            self.prompt(f"research {tag} step {i}", sid)
        return sid

    def test_reuses_an_existing_name_and_creates_a_new_one(self) -> None:
        row = self.named("research")
        self.assertEqual(row["files"], ["handoffs/research.md"])
        self.assertEqual(row["file_choice"], "research")
        self.assertIn("Existing: research.", self.calls[0]["prompt"])
        sid = self.other("aaaa1111")
        row = self.named("token-costs", sid)
        self.assertEqual(row["files"], ["handoffs/token-costs.md"])
        self.assertIn(sid[:8], self.read("handoffs/token-costs.md"))

    def test_bad_slugs_fall_back_to_misc(self) -> None:
        os.makedirs(os.path.join(self.projects, "alpha"))
        for n, slug in enumerate(("../x", "misc", "ABC-123456", "alpha", "BadSlug", "")):
            with self.subTest(slug=slug):
                sid = self.other(f"bbbb{n:04d}")
                self.assertEqual(self.named(slug, sid)["files"], ["handoffs/misc.md"], slug)

    def test_missing_file_line_goes_to_misc(self) -> None:
        self.assertEqual(self.run_card(self.card())["files"], ["handoffs/misc.md"])


class LateRun(CardCase):
    def setUp(self) -> None:
        super().setUp()
        self.dirty()
        self.projects_root = os.path.join(self.sb.root, "claude-projects")
        os.makedirs(os.path.join(self.projects_root, "proj"))
        self.transcript = os.path.join(self.projects_root, "proj", SID + ".jsonl")
        with open(self.transcript, "w", encoding="utf-8") as handle:
            handle.write(transcript_line("assistant", [{"type": "text", "text": "ok"}]) + "\n")
        self.later = time.time() + 7200
        self.spawn = mock.patch.object(cardjob, "spawn")

    def stub_pass(self) -> object:
        with self.spawn:
            return stubs.stub_ended_segments(self.cfg(), OTHER_SID, self.later)

    def test_quiet_segment_gets_a_late_run_not_a_stub(self) -> None:
        self.assertIsNone(self.stub_pass())
        job = cardjob.read_job(self.cfg(), SID)
        self.assertEqual(
            (job["status"], job["reason"], job["transcript"]), ("pending", "late", self.transcript)
        )
        self.assertEqual(self.handoff(), "")

    def test_late_run_fills_the_missing_card(self) -> None:
        self.stub_pass()
        cardrun.run_locked(self.cfg(), SID, cardjob.read_job(self.cfg(), SID), self.model(card()))
        self.assertIn("refund rounding", self.handoff())
        self.assertFalse(self.seg().dirty)

    def test_missing_transcript_and_exhausted_budget_get_a_stub(self) -> None:
        os.unlink(self.transcript)
        self.assertIsNotNone(self.stub_pass())
        self.assertIn("stub", self.handoff())

    def test_budget_exhausted_gets_a_stub(self) -> None:
        runs = os.path.join(self.sb.state, "cards")
        os.makedirs(runs, exist_ok=True)
        with open(os.path.join(runs, "runs.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"t": self.later, "cost_usd": 1.99}) + "\n")
        with mock.patch("time.time", return_value=self.later):
            self.assertIsNotNone(self.stub_pass())
        self.assertIn("stub", self.handoff())

    def test_failed_late_run_falls_back_to_a_stub_once(self) -> None:
        self.stub_pass()
        cardrun.run_locked(self.cfg(), SID, cardjob.read_job(self.cfg(), SID), self.model("junk"))
        self.assertIsNotNone(self.stub_pass())
        self.assertIn("stub", self.handoff())


class JobStampPrecision(CardCase):
    def start_job_at(self, wall: float) -> None:
        with (
            mock.patch("time.time", return_value=wall),
            mock.patch.object(cardjob, "spawn"),
        ):
            cardjob.start_run(self.cfg(), SID, "late", None)

    def test_job_started_in_the_same_millisecond_as_the_last_event_counts_as_ran(self) -> None:
        with mock.patch("time.time", return_value=1794026257.2148):
            self.dirty()
        self.assertEqual(self.seg().last_t, 1794026257.215)
        self.start_job_at(1794026257.214817)
        self.assertTrue(cardjob.already_ran(self.cfg(), self.seg()))

    def test_card_with_a_malformed_heading_is_still_written(self) -> None:
        self.dirty()
        bad = card(body="Bad head.").replace("## ", "### ", 1).replace(f" · {SEG8}", "", 1)
        row = self.run_card(bad)
        self.assertEqual(row["status"], "ok")
        self.assertIn("Bad head.", self.handoff())

    def test_job_started_before_the_last_event_does_not(self) -> None:
        with mock.patch("time.time", return_value=1794026257.2148):
            self.dirty()
        self.start_job_at(1794026257.2141)
        self.assertFalse(cardjob.already_ran(self.cfg(), self.seg()))


class Validation(unittest.TestCase):
    def check(self, text: str) -> str:
        return cardfmt.validate_card(text, "abcd1234", "ABC-1", "2026-02-02 10:00")

    def test_accepts_and_sets_time(self) -> None:
        out = self.check(card(seg="abcd1234", key="ABC-1"))
        self.assertTrue(
            out.startswith("## 2026-02-02 10:00 · ABC-1 · refund rounding · abcd1234\n")
        )

    def test_rejections(self) -> None:
        good = card(seg="abcd1234", key="ABC-1")
        cases = {
            "wrong seg": good.replace("abcd1234", "ffff0000", 1),
            "wrong key": good.replace("ABC-1 ·", "ABC-2 ·", 1),
            "extra heading": good.replace("Next:", "## Next\nNext:"),
            "auto": good.replace("Learned", "auto: true\nLearned"),
            "no repos": good.replace("Repos: billing-api\n", ""),
            "no learned": good.replace("Learned: rounding belongs in the money type\n", ""),
            "secret": good.replace("tests pass", "key ghp_" + "b" * 36),
            "you": good.replace("tests pass", "you should"),
            "long": good + "x" * 3000,
            "lines": good + "a\n" * 20,
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(cardfmt.Failed):
                self.check(text)

    def test_malformed_headings_are_rebuilt_from_the_engine_values(self) -> None:
        want = "## 2026-02-02 10:00 · ABC-1 · refund rounding · abcd1234\n"
        body = card(seg="abcd1234", key="ABC-1").split("\n", 1)[1]
        for name, head in {
            "level 3": "### 2026-02-02 10:00 · ABC-1 · refund rounding · abcd1234",
            "date only": "## 2026-02-02 · ABC-1 · refund rounding · abcd1234",
            "wrong seg": "## 2026-02-02 10:00 · ABC-1 · refund rounding · ffff0000",
            "wrong key": "## 2026-02-02 10:00 · ABC-2 · refund rounding · abcd1234",
            "no seg": "## 2026-02-02 10:00 · ABC-1 · refund rounding",
            "no key": "## 2026-02-02 10:00 · refund rounding · abcd1234",
        }.items():
            with self.subTest(name):
                fixed = cardfmt.normalise_heading(
                    head + "\n" + body, "abcd1234", "ABC-1", "2026-02-02 10:00"
                )
                self.assertTrue(self.check(fixed).startswith(want), fixed[:90])

    def test_a_heading_with_no_label_is_left_to_fail(self) -> None:
        text = "## 2026-02-02 10:00 · ABC-1 · abcd1234\nRepos: r\n"
        self.assertEqual(cardfmt.normalise_heading(text, "abcd1234", "ABC-1", "x"), text)
        with self.assertRaises(cardfmt.Failed):
            self.check(text)

    def test_strips_fence_and_file_line(self) -> None:
        chosen, body = cardfmt.split_file_line("```\nFILE: research\n\n## head\nbody\n```")
        self.assertEqual((chosen, body), ("research", "## head\nbody"))

    def test_links_without_a_reason_are_unlinked(self) -> None:
        text = "## h\nLinks: [[a]], [[b]] (the run commands)\nSee [[c|C]] for details\nok [[d]]"
        fixed, unlinked = cardfmt.fix_links(text)
        self.assertEqual(unlinked, 2)
        self.assertIn("Links: [[b]] (the run commands)", fixed)
        self.assertIn("See [[c|C]] for details", fixed)
        self.assertIn("ok d", fixed)
        self.assertNotIn("[[a]]", fixed)
        only_bad, _ = cardfmt.fix_links("## h\nLinks: [[a]]\nx")
        self.assertNotIn("Links", only_bad)


class LearningMoments(unittest.TestCase):
    USER = ("wrong", "again", "don't")
    SELF = ("actually", "my mistake")

    def moments(self, events: list[digest.Event]) -> str:
        return digest.learning_moments(events, list(self.USER), list(self.SELF), "none")

    def test_user_correction_with_context(self) -> None:
        events = [
            digest.Event("assistant", "I will use the staging profile"),
            digest.Event("user", "that is wrong, use prod"),
            digest.Event("assistant", "switching to prod"),
        ]
        out = self.moments(events)
        self.assertIn('user said "that is wrong, use prod"', out)
        self.assertIn('after assistant: "I will use the staging profile"', out)
        self.assertIn('then assistant: "switching to prod"', out)

    def test_cue_is_a_whole_word_prefix(self) -> None:
        self.assertEqual(self.moments([digest.Event("user", "a stronger plan")]), "")

    def test_self_correction_and_fix_pairs(self) -> None:
        events = [
            digest.Event("assistant", "Actually, the root is in config"),
            digest.Event("bash", "make test", True, "boom\nline"),
            digest.Event("bash", "make test FOO=1", False, "ok"),
        ]
        out = self.moments(events)
        self.assertIn("assistant admitted:", out)
        self.assertIn(
            "command failed: $ make test -> boom | line; fixed by: $ make test FOO=1", out
        )

    def test_failure_without_a_later_success_is_no_moment(self) -> None:
        self.assertEqual(self.moments([digest.Event("bash", "x", True, "e")]), "")

    def test_hook_denial_counts_as_a_failure(self) -> None:
        facts = digest.facts_from_lines(
            [
                transcript_line(
                    "assistant",
                    [{"type": "tool_use", "name": "Bash", "id": "1", "input": {"command": "rm x"}}],
                ),
                transcript_line(
                    "user",
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": "1",
                            "content": "PreToolUse hook blocked by policy",
                        }
                    ],
                ),
            ],
            "/h",
        )
        self.assertTrue(facts.events[0].failed)

    def test_sidechain_and_meta_are_ignored(self) -> None:
        facts = digest.facts_from_lines(
            [
                transcript_line("user", "wrong wrong", isSidechain=True),
                transcript_line("user", "wrong meta", isMeta=True),
            ],
            "/h",
        )
        self.assertEqual(facts.events, [])

    def test_caps_and_mix(self) -> None:
        events = [digest.Event("user", f"wrong {i}") for i in range(12)]
        events += [digest.Event("assistant", f"actually {i}") for i in range(6)]
        out = self.moments(events)
        lines = [row for row in out.splitlines() if row.startswith("- ")]
        self.assertLessEqual(len(lines), digest.MOMENTS_KEEP)
        self.assertTrue(any("assistant admitted" in row for row in lines))

    def test_moments_survive_the_digest_cap(self) -> None:
        from juno.core.ledger import Segment

        seg = Segment("s" * 8)
        facts = digest.Facts(assistant=["x" * 500] * 3, events=[digest.Event("user", "wrong path")])
        out = digest.build_digest(seg, facts, "", 800, list(self.USER), list(self.SELF))
        self.assertLessEqual(len(out), 800)
        self.assertIn("Learning moments", out)


if __name__ == "__main__":
    unittest.main()
