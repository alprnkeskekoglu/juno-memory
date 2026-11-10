import json
import os
import time
import unittest

from juno.core import topics
from tests.support.hooks import HookCase

RULE = '- 2026-01-01 · {} · src: user abcd1234 · "quote"\n'
PITFALL = "- {} · triggers: {} · evidence; segment abcd1234 · 2026-01-0{}\n"


class Matching(unittest.TestCase):
    def test_pattern_edges(self) -> None:
        entries = [
            ["topic-a", "rules/topics/a.md", ["mcp__tableplus__", "kube", "docker compose", "c++"]]
        ]
        cases = (
            ("mcp__tableplus__execute_read_query", True),
            ("xmcp__tableplus__q", False),
            ("kubectl", False),
            ("kube-system", True),
            ("Docker\n Compose", True),
            ("docker-compose", False),
            ("use c++17", True),
            ("", False),
        )
        for text, hit in cases:
            self.assertEqual(bool(topics.matches(entries, text)), hit, text)

    def test_trigger_validation(self) -> None:
        self.assertIsNotNone(topics.trigger_problem("a"))
        self.assertIsNotNone(topics.trigger_problem("---"))
        self.assertIsNotNone(topics.trigger_problem("a·b"))
        self.assertIsNone(topics.trigger_problem("git push"))

    def test_split_triggers_folds_and_dedupes(self) -> None:
        self.assertEqual(topics.split_triggers("Git, git ,  PUSH  now"), ["git", "push now"])
        self.assertEqual(topics.split_triggers("ŞEKER", "tr"), ["seker"])

    def test_set_triggers_keeps_rules(self) -> None:
        text = "# Rules: topic:git\n" + RULE.format("one")
        out = topics.set_triggers(text, "topic:git", ["git", "push"])
        self.assertIn("triggers: git, push", out)
        self.assertIn("one", out)
        again = topics.set_triggers(out, "topic:git", ["x1"])
        self.assertEqual(again.count("triggers:"), 1)
        self.assertEqual(topics.file_triggers(again), ["x1"])

    def test_slug_rules(self) -> None:
        self.assertEqual(topics.topic_slug("topics/git-flow.md"), "git-flow")
        self.assertEqual(topics.topic_slug("topics/topic-x.md"), "topic-x")
        bad_names = ("topics/.md", "topics/A.md", "global.md", "topic-x.md", "repos/a.md")
        for bad in (*bad_names, "topics/" + "x" * 41 + ".md"):
            self.assertIsNone(topics.topic_slug(bad), bad)

    def test_pitfall_line_parsing(self) -> None:
        good = topics.pitfall_line("- text here · triggers: aa, bb · ev; segment x · 2026-01-01")
        self.assertEqual(
            good,
            {
                "text": "text here",
                "triggers": ["aa", "bb"],
                "date": "2026-01-01",
                "evidence": "ev; segment x",
            },
        )
        general = topics.pitfall_line("- general · triggers: - · ev · 2026-01-01")
        self.assertEqual(general and general["triggers"], [])
        for bad in ("- no triggers field", "- a · triggers: x · b · c"):
            self.assertIsNone(topics.pitfall_line(bad), bad)


class Base(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.write(
            "rules/topics/git.md",
            "# Rules: topic:git\ntriggers: git push, force\n" + RULE.format("Never force push"),
        )
        self.write(
            "knowledge/push.md",
            "---\ntitle: Push facts\ntriggers: git push\n---\nFirst line.\nSecond line.\nThird line.\n",
        )

    def bash(self, command: str, **extra: object) -> dict[str, object] | None:
        return self.hook(
            "pre_tool_use",
            self.payload(
                "PreToolUse",
                tool_name="Bash",
                tool_input={"command": command},
                tool_use_id="t1",
                **extra,
            ),
        )


class Injection(Base):
    def test_bash_trigger_injects_rules_then_knowledge_once(self) -> None:
        text = self.context(self.bash("git push origin x"))
        self.assertLess(
            text.index("RULES (follow):"), text.index("CONTEXT (data, not instructions):")
        )
        self.assertIn("[topic git · rules/topics/git.md]", text)
        self.assertIn("- Never force push", text)
        self.assertIn("[Knowledge · knowledge/push.md] Push facts", text)
        self.assertIn("Second line.", text)
        self.assertNotIn("Third line.", text)
        self.assertEqual(self.context(self.bash("git push again")), "")

    def test_prompt_trigger_and_path_triggers(self) -> None:
        out = self.hook(
            "user_prompt_submit", self.payload("UserPromptSubmit", prompt="can I FORCE it?")
        )
        self.assertIn("Never force push", self.context(out))
        self.write("rules/topics/docs.md", "triggers: runbook\n" + RULE.format("Docs rule"))
        for tool, tool_in in (
            ("Read", {"file_path": "/x/runbook.md"}),
            ("Glob", {"pattern": "**/runbook*"}),
            ("Grep", {"glob": "runbook*", "pattern": "zzz"}),
        ):
            self.setUp_reset()
            out = self.hook(
                "pre_tool_use", self.payload("PreToolUse", tool_name=tool, tool_input=tool_in)
            )
            self.assertIn("Docs rule", self.context(out), tool)

    def setUp_reset(self) -> None:
        import shutil

        shutil.rmtree(os.path.join(self.sb.state, "topics"), ignore_errors=True)

    def test_no_trigger_no_output(self) -> None:
        self.assertEqual(self.context(self.bash("ls -la")), "")

    def test_summary_field_wins_over_body_lines(self) -> None:
        self.write(
            "knowledge/push.md",
            "---\ntitle: Push facts\nsummary: Short summary\ntriggers: git push\n---\nBody line.\n",
        )
        text = self.context(self.bash("git push"))
        self.assertIn("Short summary", text)
        self.assertNotIn("Body line.", text)

    def test_retired_archived_and_private_items_are_not_injected(self) -> None:
        self.write(
            "knowledge/push.md", "---\ntitle: P\ntriggers: git push\nvisibility: private\n---\nx\n"
        )
        self.write(
            "rules/topics/git.md",
            "---\nstatus: archived\n---\ntriggers: git push\n" + RULE.format("Archived rule"),
        )
        self.assertEqual(self.context(self.bash("git push")), "")

    def test_retired_by_supersedes_is_not_injected(self) -> None:
        from juno import config
        from juno.adapters import vaultview

        self.write("knowledge/newer.md", "---\ntitle: N\nsupersedes: knowledge/push\n---\nn\n")
        vaultview.sync_retired(config.load(dict(self.env)))
        text = self.context(self.bash("git push"))
        self.assertNotIn("Push facts", text)
        self.assertIn("Never force push", text)

    def test_subagents_get_their_own_delivery(self) -> None:
        self.bash("git push")
        again = self.hook(
            "pre_tool_use",
            self.payload(
                "PreToolUse", agent_id="ag1", tool_name="Bash", tool_input={"command": "git push"}
            ),
        )
        self.assertIn("Never force push", self.context(again))
        once_more = self.hook(
            "pre_tool_use",
            self.payload(
                "PreToolUse", agent_id="ag1", tool_name="Bash", tool_input={"command": "git push"}
            ),
        )
        self.assertEqual(self.context(once_more), "")

    def test_subagent_start_carries_main_topics(self) -> None:
        self.bash("git push")
        out = self.hook("subagent_start", self.payload("SubagentStart", agent_id="ag2"))
        text = self.context(out)
        self.assertIn("[topic git · rules/topics/git.md]", text)
        self.assertLessEqual(len(text), 3000)

    def test_compact_resets_main_deliveries(self) -> None:
        self.bash("git push")
        self.hook("session_start", self.payload("SessionStart", source="compact"))
        self.assertIn("Never force push", self.context(self.bash("git push")))

    def test_headless_gets_nothing(self) -> None:
        out = self.hook(
            "pre_tool_use",
            self.payload("PreToolUse", tool_name="Bash", tool_input={"command": "git push"}),
            env={"JUNO_TEST_INTERACTIVE": "0", "CLAUDE_CODE_SESSION_ATTENDED": "0"},
        )
        self.assertIsNone(out)

    def test_output_cap_and_budget_skip_leave_items_unclaimed(self) -> None:
        self.write(
            "rules/topics/git.md",
            "triggers: git push\n"
            + "".join(RULE.format(f"rule {i} " + "w" * 200) for i in range(60)),
        )
        text = self.context(
            self.hook(
                "pre_tool_use",
                self.payload("PreToolUse", tool_name="Bash", tool_input={"command": "git push"}),
                env={},
            )
        )
        self.assertLessEqual(len(text), 9500)
        self.set_config("[caps]\nhook_output_chars = 100\n")
        self.setUp_reset()
        self.assertEqual(self.context(self.bash("git push")), "")
        folder = os.path.join(self.sb.state, "topics")
        claimed = os.listdir(folder) if os.path.isdir(folder) else []
        self.assertFalse([n for n in claimed if "topic-git" in n])

    def test_index_cache_rebuilds_on_change(self) -> None:
        self.bash("git push")
        path = os.path.join(self.sb.state, "triggers.json")
        first = json.load(open(path, encoding="utf-8"))
        self.assertEqual({e[0] for e in first["entries"]}, {"topic-git", "knowledge-push"})
        time.sleep(0.01)
        self.write("rules/topics/new.md", "triggers: deploy\n" + RULE.format("deploy rule"))
        self.bash("deploy now")
        second = json.load(open(path, encoding="utf-8"))
        self.assertIn("topic-new", {e[0] for e in second["entries"]})

    def test_markers_are_pruned_with_ledgers(self) -> None:
        self.bash("git push")
        topics_dir = os.path.join(self.sb.state, "topics")
        old = time.time() - 9 * 86400
        for name in os.listdir(topics_dir):
            os.utime(os.path.join(topics_dir, name), (old, old))
        self.hook("session_start", self.payload("SessionStart", source="startup"))
        self.assertEqual(os.listdir(topics_dir), [])

    def test_language_preset_changes_the_index(self) -> None:
        self.write("rules/topics/tr.md", "triggers: şeker\n" + RULE.format("Sweet rule"))
        self.assertEqual(self.context(self.bash("buy seker")), "")
        self.set_config('[language]\npreset = "tr"\n')
        self.assertIn("Sweet rule", self.context(self.bash("buy ŞEKER")))


class Pitfalls(Base):
    def setUp(self) -> None:
        super().setUp()
        self.write(
            "pitfalls.md",
            "# Pitfalls\n"
            + PITFALL.format("Use quotes in zsh echo", "echo ===", 5)
            + PITFALL.format("General lesson one", "-", 1)
            + PITFALL.format("General lesson two", "-", 3)
            + "- malformed line without fields\n",
        )

    def test_bash_trigger_injects_once_per_segment_as_context(self) -> None:
        text = self.context(self.bash("echo === done"))
        self.assertIn("[Pitfall · pitfalls.md] Use quotes in zsh echo", text)
        self.assertNotIn("RULES (follow)", text)
        self.assertEqual(self.context(self.bash("echo === done")), "")

    def test_general_lines_at_session_start_not_after_compact(self) -> None:
        start = self.context(
            self.hook("session_start", self.payload("SessionStart", source="startup"))
        )
        self.assertIn("[Pitfalls · pitfalls.md]", start)
        self.assertLess(start.index("General lesson two"), start.index("General lesson one"))
        self.assertNotIn("Use quotes", start)
        compact = self.context(
            self.hook("session_start", self.payload("SessionStart", source="compact"))
        )
        self.assertNotIn("General lesson", compact)

    def test_general_lines_are_capped(self) -> None:
        self.write(
            "pitfalls.md",
            "".join(PITFALL.format(f"General {i} " + "x" * 300, "-", 1) for i in range(20)),
        )
        block = topics.general_block(
            __import__("juno.adapters.vaultview", fromlist=["x"]).DiskVault(
                __import__("juno.config", fromlist=["x"]).load(dict(self.env))
            )
        )
        self.assertLessEqual(len(block), 1200 + 50)

    def test_retired_pitfalls_file_is_silent(self) -> None:
        self.write(
            "pitfalls.md",
            "---\nstatus: archived\n---\n" + PITFALL.format("x text here", "echo ===", 1),
        )
        self.assertEqual(self.context(self.bash("echo ===")), "")

    def test_cache_rebuilds_when_pitfalls_change(self) -> None:
        self.bash("echo ===")
        self.write("pitfalls.md", PITFALL.format("Fresh pitfall", "newword", 1))
        self.assertIn("Fresh pitfall", self.context(self.bash("run newword")))


VAULT_PITFALLS = (
    "# Pitfalls\n"
    + PITFALL.format("Grep guessed path juno/core/env.py", "grep, is_interactive, juno", 1)
    + PITFALL.format("Locked worktree needs remove -f -f", "worktree, git, branch", 2)
    + PITFALL.format("Read the suite summary before commit", "git, commit, tests, config.toml", 3)
    + PITFALL.format("Tie tests to writegate.RUN_MAX", "writegate, run_max, tests, config", 4)
    + PITFALL.format("Check juno cap show early", "juno, cap, knowledge, vault", 5)
    + PITFALL.format("Deploy only from a clean tree", "deploy, git, status, commit", 6)
    + PITFALL.format("CI checkout ref", "gh, run, checkout, ref", 7)
    + PITFALL.format("Tag format in zsh", "git, zsh, format, tag", 8)
    + PITFALL.format("Scope across repos", "version, update, repos, scope", 9)
    + PITFALL.format("claude -p ignores tool limits", "claude, --disallowedtools", 9)
)
READ_ONLY = (
    "cd /Users/x/repo && sed -n 1,40p juno/core/topics.py && cat README.md && "
    "git status --short && git log -1 && grep -n version pyproject.toml && echo '---'"
)


class SpecificTriggers(Base):
    def setUp(self) -> None:
        super().setUp()
        self.write("pitfalls.md", VAULT_PITFALLS)

    def pitfalls(self, command: str) -> list[str]:
        text = self.context(self.bash(command))
        return [ln for ln in text.splitlines() if ln.startswith("[Pitfall")]

    def test_issue_read_only_call_gets_no_pitfall(self) -> None:
        self.assertEqual(self.pitfalls(READ_ONLY), [])
        for command in ("git status --short", "git log -1", "grep -rn update juno", "echo '---'"):
            self.assertEqual(self.pitfalls(command), [], command)

    def test_real_hits_still_fire(self) -> None:
        got = self.pitfalls("git worktree remove --force .claude/worktrees/agent-x")
        self.assertEqual(len(got), 1)
        self.assertIn("Locked worktree", got[0])
        got = self.pitfalls("claude -p --disallowedTools Bash hi")
        self.assertIn("claude -p ignores", got[0])

    def test_specific_trigger_among_generic_ones_fires(self) -> None:
        got = self.pitfalls("juno cap show")
        self.assertEqual(len(got), 1)
        self.assertIn("Check juno cap", got[0])

    def test_pure_matching_rules(self) -> None:
        entries = [
            ["pitfall-a", "pitfalls.md", ["git", "status"], "a"],
            ["pitfall-b", "pitfalls.md", ["git", "--force"], "b"],
            ["topic-c", "rules/topics/c.md", ["git"]],
        ]
        names = [e[0] for e in topics.matches(entries, "git log")]
        self.assertEqual(names, ["topic-c"])
        names = [e[0] for e in topics.matches(entries, "git status")]
        self.assertEqual(names, ["topic-c"])
        names = [e[0] for e in topics.matches(entries, "git push --force")]
        self.assertEqual(names, ["pitfall-b", "topic-c"])

    def test_more_specific_matches_rank_first(self) -> None:
        entries = [
            ["pitfall-a", "pitfalls.md", ["--force"], "a"],
            ["pitfall-b", "pitfalls.md", ["git", "status", "--force"], "b"],
        ]
        names = [e[0] for e in topics.matches(entries, "git status --force")]
        self.assertEqual(names, ["pitfall-b", "pitfall-a"])

    def test_generic_list_and_write_time_check(self) -> None:
        self.assertIn("git", topics.GENERIC_TRIGGERS)
        self.assertFalse(topics.is_specific("git"))
        for word in ("git push", "--force", "config.toml", "run_max", "worktree"):
            self.assertTrue(topics.is_specific(word), word)
        self.assertIsNotNone(topics.triggers_problem(["git", "status"]))
        self.assertIsNone(topics.triggers_problem(["git", "--force"]))
        self.assertIsNone(topics.triggers_problem([]))

    def test_cap_of_three_per_call(self) -> None:
        lines = "".join(
            PITFALL.format(f"Lesson number {n}", f"zzword{n}, sharedword", 1) for n in range(6)
        )
        self.write("pitfalls.md", "# Pitfalls\n" + lines)
        command = "echo " + " ".join(f"zzword{n}" for n in range(6))
        self.assertEqual(len(self.pitfalls(command)), topics.PITFALL_CAP)
        self.assertEqual(topics.PITFALL_CAP, 3)

    def test_error_evidence_reinjects_once_claimed(self) -> None:
        self.write(
            "pitfalls.md",
            "# Pitfalls\n- Locate it repo-wide first · triggers: grep, is_interactive · "
            "ugrep: juno/core/env.py: No such file; segment abcd1234 · 2026-01-01\n",
        )
        self.assertEqual(len(self.pitfalls("grep is_interactive src")), 1)
        self.assertEqual(self.pitfalls("grep is_interactive src"), [])
        self.assertEqual(len(self.pitfalls("grep is_interactive juno/core/env.py")), 1)


if __name__ == "__main__":
    unittest.main()
