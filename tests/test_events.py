import json
import os
import unittest

from tests.support.hooks import OTHER_SID, HookCase, git

TASK = (
    "---\ntitle: Zebra task\nstatus: open\nrepos: [alpha]\n---\n\n## Notes\n- remember the zebra\n"
)
PROFILE = "# alpha\n\n## Purpose\nAlpha purpose line.\n\n## Gotchas\n- alpha gotcha one\n\n## How to run\n- make run\n"
CARD = (
    "# Handoffs · alpha\n\n## 2026-10-01 10:00 · - · alpha · abcd1234\nRepos: alpha\nTickets: -\n"
    "Did the alpha thing.\n**Decision:** keep alpha simple\nLearned: alpha is simple\n"
)


class Repos(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("projects/alpha.md", PROFILE)
        self.write(
            "rules/repos/alpha.md", '- 2026-01-01 · Alpha rule text · src: user abcd1234 · "q"\n'
        )
        self.write("handoffs/alpha.md", CARD)
        self.write(
            "decisions/alpha.md",
            "---\ntopic: alpha\n---\n## Current\n- use alpha (user, 2026-01-01)\n\n## History\n- old alpha\n",
        )
        self.repo = self.make_repo("alpha", "feature/ABC-100")
        self.write("tasks/ABC-100.md", TASK)

    def test_session_start_in_repo_gives_rules_then_pack(self) -> None:
        out = self.hook(
            "session_start", self.payload("SessionStart", cwd=self.repo, source="startup")
        )
        text = self.context(out)
        self.assertTrue(text.startswith("RULES (follow):"))
        self.assertIn("=== Juno pack: repo alpha ===", text)
        self.assertIn("Alpha rule text", text)
        self.assertIn("alpha gotcha one", text)
        self.assertIn("use alpha", text)
        self.assertNotIn("old alpha", text)
        self.assertIn("=== Juno pack: ticket ABC-100 ===", text)
        self.assertIn("remember the zebra", text)
        self.assertLessEqual(len(text), 9500)
        self.assertLess(
            text.index("RULES (follow)"), text.index("CONTEXT (data, not instructions)")
        )

    def test_session_start_records_start_and_packs(self) -> None:
        self.hook("session_start", self.payload("SessionStart", cwd=self.repo, source="startup"))
        events = self.ledger()
        self.assertEqual(events[0]["ev"], "start")
        self.assertEqual(events[0]["repos"], [["alpha", "ABC-100"]])
        self.assertEqual(
            [(e["key"], e["mode"]) for e in events if e["ev"] == "pack"],
            [("ticket:ABC-100", "full"), ("repo:alpha", "full")],
        )

    def test_session_start_outside_repo_has_segment_line_only(self) -> None:
        text = self.context(
            self.hook("session_start", self.payload("SessionStart", source="startup"))
        )
        self.assertIn("Juno segment id: 57fb7685", text)
        self.assertTrue(os.path.isabs(self.sb.vault))
        self.assertIn(f"Juno vault: {self.sb.vault}.", text)
        self.assertNotIn("=== Juno pack", text)
        self.assertNotIn("overview", text)

    def test_overview_only_at_overview_root(self) -> None:
        text = self.context(
            self.hook(
                "session_start", self.payload("SessionStart", cwd=self.projects, source="startup")
            )
        )
        self.assertIn("=== Juno overview", text)
        self.assertIn("ABC-100 · Zebra task", text)
        self.assertIn("Did the alpha thing", text)

    def test_overview_without_jira_has_no_ticket_part(self) -> None:
        sandbox_config = self.sb.config
        with open(sandbox_config, encoding="utf-8") as handle:
            kept = handle.read().replace('prefixes = ["ABC", "DEF"]', "prefixes = []")
        with open(sandbox_config, "w", encoding="utf-8") as handle:
            handle.write(kept)
        text = self.context(
            self.hook(
                "session_start", self.payload("SessionStart", cwd=self.projects, source="startup")
            )
        )
        self.assertIn("=== Juno overview", text)
        self.assertNotIn("Active tickets", text)

    def test_first_touch_pack_once_by_post_tool_use(self) -> None:
        payload = self.payload(
            "PostToolUse",
            tool_name="Read",
            tool_input={"file_path": os.path.join(self.repo, "a.py")},
            tool_response={},
        )
        first = self.context(self.hook("post_tool_use", payload))
        self.assertIn("=== Juno pack: repo alpha ===", first)
        self.assertEqual(self.context(self.hook("post_tool_use", payload)), "")
        tools = [e for e in self.ledger() if e["ev"] == "tool"]
        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0]["repos"], [["alpha", "ABC-100"]])

    def test_four_full_packs_then_pointers(self) -> None:
        for name in ("b1", "b2", "b3", "b4", "b5"):
            self.write(f"projects/{name}.md", f"# {name}\n\n## Gotchas\n- gotcha {name}\n")
            self.make_repo(name)
        seen = []
        for name in ("b1", "b2", "b3", "b4", "b5"):
            payload = self.payload(
                "PostToolUse",
                tool_name="Read",
                tool_input={"file_path": os.path.join(self.projects, name, "x.py")},
                tool_response={},
            )
            seen.append(self.context(self.hook("post_tool_use", payload)))
        self.assertIn("=== Juno pack: repo b4 ===", seen[3])
        self.assertTrue(seen[4].startswith("Juno pointer"))
        self.assertIn("projects/b5.md", seen[4])

    def test_post_tool_use_in_subagent_records_but_does_not_inject(self) -> None:
        payload = self.payload(
            "PostToolUse",
            agent_id="a1b2c3",
            tool_name="Read",
            tool_input={"file_path": os.path.join(self.repo, "a.py")},
            tool_response={},
        )
        self.assertIsNone(self.hook("post_tool_use", payload))
        tool = [e for e in self.ledger() if e["ev"] == "tool"][0]
        self.assertTrue(tool["agent"])

    def test_edit_in_repo_counts_as_edit_and_vault_edit_does_not(self) -> None:
        edit = self.payload(
            "PostToolUse",
            tool_name="Edit",
            tool_input={"file_path": os.path.join(self.repo, "a.py")},
            tool_response={},
        )
        self.hook("post_tool_use", edit)
        vault_edit = self.payload(
            "PostToolUse",
            tool_name="Edit",
            tool_input={"file_path": self.vp("notes/x.md")},
            tool_response={},
        )
        self.hook("post_tool_use", vault_edit)
        tools = [e for e in self.ledger() if e["ev"] == "tool"]
        self.assertEqual(tools[0]["edits"], [["alpha", "ABC-100"]])
        self.assertNotIn("edits", tools[1])

    def test_bash_path_under_extra_root_records_repo(self) -> None:
        other = self.make_repo("maestro", root=self.extra)
        payload = self.payload(
            "PostToolUse",
            tool_name="Bash",
            tool_input={"command": f"git -C {other} status"},
            tool_response={"stdout": ""},
        )
        self.hook("post_tool_use", payload)
        tool = [e for e in self.ledger() if e["ev"] == "tool"][0]
        self.assertIn(["maestro", None], tool["repos"])

    def test_unexpanded_variable_in_bash_records_no_repo(self) -> None:
        payload = self.payload(
            "PostToolUse",
            tool_name="Bash",
            tool_input={"command": f"git -C {self.projects}/$repo status"},
            tool_response={"stdout": ""},
        )
        self.hook("post_tool_use", payload)
        self.assertEqual([e for e in self.ledger() if e["ev"] == "tool"][0]["repos"], [])

    def test_commit_marks_boundary_and_dirty(self) -> None:
        payload = self.payload(
            "PostToolUse",
            tool_name="Bash",
            tool_input={"command": "git commit -m x"},
            tool_response={"stdout": ""},
        )
        self.hook("post_tool_use", payload)
        self.assertTrue([e for e in self.ledger() if e["ev"] == "tool"][0]["boundary"])


class Prompts(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("tasks/ABC-100.md", TASK)
        self.write(
            "decisions/ABC-100.md", "## Current\n- ticket decision line (user, 2026-01-01)\n"
        )

    def test_ticket_pack_and_cue(self) -> None:
        out = self.hook(
            "user_prompt_submit",
            self.payload("UserPromptSubmit", prompt="never skip ABC-100 checks"),
        )
        text = self.context(out)
        self.assertTrue(text.startswith("If this is a lasting correction"))
        self.assertIn("=== Juno pack: ticket ABC-100 ===", text)
        self.assertIn("ticket decision line", text)
        prompt = [e for e in self.ledger() if e["ev"] == "prompt"][0]
        self.assertEqual(prompt["tickets"], ["ABC-100"])

    def test_plain_prompt_records_turn_only(self) -> None:
        self.assertIsNone(
            self.hook("user_prompt_submit", self.payload("UserPromptSubmit", prompt="hello"))
        )
        self.assertEqual([e["ev"] for e in self.ledger()], ["prompt"])

    def test_synthetic_prompt_is_not_stored_and_gets_nothing(self) -> None:
        prompt = "<task-notification>always never ABC-100 [nosave]</task-notification>"
        self.assertIsNone(
            self.hook("user_prompt_submit", self.payload("UserPromptSubmit", prompt=prompt))
        )
        self.assertEqual([e for e in self.ledger() if e["ev"] == "prompt"], [])

    def test_mixed_prompt_keeps_user_part_only(self) -> None:
        prompt = "fix it <system-reminder>ABC-100 secret</system-reminder> please"
        self.hook("user_prompt_submit", self.payload("UserPromptSubmit", prompt=prompt))
        stored = [e for e in self.ledger() if e["ev"] == "prompt"][0]
        self.assertNotIn("ABC-100", stored["text"])
        self.assertEqual(stored["tickets"], [])

    def test_nosave_flag_is_stored_from_typed_text_only(self) -> None:
        self.hook("user_prompt_submit", self.payload("UserPromptSubmit", prompt="do work [nosave]"))
        self.assertTrue([e for e in self.ledger() if e["ev"] == "prompt"][0]["nosave"])

    def test_secrets_and_pastes_are_redacted_from_the_ledger(self) -> None:
        prompt = "token ghp_" + "a" * 36 + "\n```\npasted code\n```\nok"
        self.hook("user_prompt_submit", self.payload("UserPromptSubmit", prompt=prompt))
        text = [e for e in self.ledger() if e["ev"] == "prompt"][0]["text"]
        self.assertIn("[redacted:github]", text)
        self.assertNotIn("pasted code", text)

    def test_long_prompt_is_chunked_and_reassembled(self) -> None:
        self.hook("user_prompt_submit", self.payload("UserPromptSubmit", prompt="word " * 3000))
        parts = [e for e in self.ledger() if e["ev"] == "prompt"]
        self.assertGreater(len(parts), 2)
        self.assertTrue(all(len(json.dumps(e)) < 4000 for e in parts))

    def test_session_title_is_captured_once(self) -> None:
        payload = self.payload("UserPromptSubmit", prompt="hi", session_title="Release prep")
        self.hook("user_prompt_submit", payload)
        self.hook("user_prompt_submit", payload)
        self.assertEqual(
            [e["title"] for e in self.ledger() if e["ev"] == "title"], ["Release prep"]
        )

    def test_subagent_prompts_are_ignored(self) -> None:
        payload = self.payload("UserPromptSubmit", prompt="hello", agent_id="a1")
        self.assertIsNone(self.hook("user_prompt_submit", payload))
        self.assertEqual(self.ledger(), [])

    def test_no_cue_without_matching_text_and_no_ticket_without_jira(self) -> None:
        self.assertEqual(
            self.context(
                self.hook(
                    "user_prompt_submit", self.payload("UserPromptSubmit", prompt="run the tests")
                )
            ),
            "",
        )


class SessionEvents(HookCase):
    def test_headless_gets_nothing_and_writes_nothing(self) -> None:
        env = {"CLAUDE_CODE_SESSION_ATTENDED": "0", "JUNO_TEST_INTERACTIVE": "0"}
        for event in (
            "session_start",
            "user_prompt_submit",
            "post_tool_use",
            "stop",
            "session_end",
        ):
            self.assertIsNone(self.hook(event, self.payload(event, prompt="x"), env=env))
        self.assertEqual(self.ledger(), [])

    def test_disable_switch(self) -> None:
        self.assertIsNone(self.hook("session_start", env={"JUNO_DISABLE": "1"}))
        self.assertEqual(self.ledger(), [])

    def test_bad_stdin_fails_open(self) -> None:
        done = self.hook_sub("post_tool_use", {})
        self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_compact_skips_stubs_nudges_and_resets_packs(self) -> None:
        self.write("projects/alpha.md", PROFILE)
        repo = self.make_repo("alpha")
        self.hook("session_start", self.payload("SessionStart", cwd=repo, source="startup"))
        self.assertTrue(
            self.context(
                self.hook("session_start", self.payload("SessionStart", cwd=repo, source="compact"))
            )
        )
        events = [e["ev"] for e in self.ledger()]
        self.assertIn("packs_reset", events)
        self.assertEqual(events.count("pack"), 2)

    def test_previous_segment_stub_is_written_once_and_announced(self) -> None:
        from tests.test_ledger import write_ledger

        write_ledger(self.sb.state, OTHER_SID, 20, age=7200)
        first = self.context(
            self.hook("session_start", self.payload("SessionStart", source="startup"))
        )
        self.assertIn(f"Juno previous segment {OTHER_SID[:8]}", first)
        self.assertIn("`juno card ", first)
        self.assertNotIn("Improve its stub", first)
        card = self.read("handoffs/r.md")
        self.assertEqual(card.count(f"· stub · {OTHER_SID[:8]}"), 1)
        again = self.context(
            self.hook("session_start", self.payload("SessionStart", source="startup"))
        )
        self.assertNotIn("previous segment", again)
        self.assertEqual(self.read("handoffs/r.md").count("stub ·"), 1)

    def test_recent_segment_is_left_alone(self) -> None:
        from tests.test_ledger import write_ledger

        write_ledger(self.sb.state, OTHER_SID, 20, age=60)
        self.assertNotIn(
            "previous segment",
            self.context(
                self.hook("session_start", self.payload("SessionStart", source="startup"))
            ),
        )

    def test_hook_error_line_after_three_repeats(self) -> None:
        stamp = __import__("time").strftime("%Y-%m-%dT%H:%M:%S")
        os.makedirs(self.sb.state, exist_ok=True)
        with open(os.path.join(self.sb.state, "hook-errors.log"), "w", encoding="utf-8") as handle:
            for _ in range(3):
                handle.write(f"{stamp} post_tool_use KeyError core.py:12 'x'\n")
        text = self.context(
            self.hook("session_start", self.payload("SessionStart", source="startup"))
        )
        self.assertIn("hook error post_tool_use KeyError at core.py:12 x3", text)
        compact = self.context(
            self.hook("session_start", self.payload("SessionStart", source="compact"))
        )
        self.assertNotIn("hook error", compact)

    def test_retention_prunes_old_ledgers(self) -> None:
        from tests.test_ledger import write_ledger

        write_ledger(self.sb.state, OTHER_SID, 1, age=9 * 86400)
        self.hook("session_start", self.payload("SessionStart", source="startup"))
        self.assertEqual(self.ledger(OTHER_SID), [])

    def test_unrelated_git_repo_does_not_break_start(self) -> None:
        stray = os.path.join(self.work, "stray")
        os.makedirs(stray)
        git(stray, "init", "-q")
        self.assertTrue(
            self.context(self.hook("session_start", self.payload("SessionStart", cwd=stray)))
        )


if __name__ == "__main__":
    unittest.main()
