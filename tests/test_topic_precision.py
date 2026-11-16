"""Issue #36 part B: precision of topic, knowledge and pitfall matching."""

import json
import os
import time
import unittest

from juno.core import bashview, topics
from tests.support.hooks import OTHER_SID, SID, HookCase

RULE = '- 2026-01-01 · {} · src: user abcd1234 · "quote"\n'


class Filter(unittest.TestCase):
    def test_memory_cli_positionals_are_data(self) -> None:
        text, _ = bashview.bash_view('juno search "x kube namespace"')
        self.assertEqual(text, "juno search")
        text, _ = bashview.bash_view('juno rule add --scope topic:x --quote "slack kube" "a rule"')
        self.assertNotIn("kube", text)
        self.assertIn("--scope", text)

    def test_search_flag_values_and_message_bodies_are_data(self) -> None:
        for command in (
            'gh issue list --search "kube slack"',
            'gh pr comment 4 --comment "kube slack"',
            'git log --grep "kube slack"',
            'rg "kube slack" src',
        ):
            self.assertNotIn("kube", bashview.bash_view(command)[0], command)

    def test_path_words_leave_the_text(self) -> None:
        text, _, paths = bashview.bash_parts("python3 scripts/mantis_brief.py --fast")
        self.assertNotIn("mantis", text)
        self.assertEqual(paths, ("scripts/mantis_brief.py",))

    def test_path_match_is_by_whole_segment(self) -> None:
        views = topics.path_views(("/x/mantis_brief.py", "/x/docs/mantis/a.md", "/y/runbook.md"))
        cases = (
            ("mantis", True),  # the directory
            ("runbook", True),  # file name without its extension
            ("runbook.md", True),
            ("brief", False),
            ("x/docs", True),
            (".md", True),
            ("mantis_brief", True),
            ("antis", False),
        )
        for trigger, hit in cases:
            self.assertEqual(topics.path_match(trigger, views), hit, trigger)
        self.assertFalse(topics.path_match("mantis", topics.path_views(("/x/mantis_brief.py",))))


class Base(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.write(
            "rules/topics/kube.md",
            "# Rules: topic:kube\ntriggers: kube, kube-system, slack\n"
            + RULE.format("Use the staging context for deployments"),
        )

    def bash(self, command: str) -> str:
        out = self.hook(
            "pre_tool_use",
            self.payload(
                "PreToolUse", tool_name="Bash", tool_input={"command": command}, tool_use_id="t1"
            ),
        )
        return self.context(out)

    def read_file(self, path: str) -> str:
        out = self.hook(
            "pre_tool_use",
            self.payload("PreToolUse", tool_name="Read", tool_input={"file_path": path}),
        )
        return self.context(out)

    def prompt(self, text: str) -> str:
        out = self.hook("user_prompt_submit", self.payload("UserPromptSubmit", prompt=text))
        return self.context(out)

    def reset(self) -> None:
        import shutil

        shutil.rmtree(os.path.join(self.sb.state, "topics"), ignore_errors=True)


class Precision(Base):
    def test_criterion_3_search_query_injects_no_topic_rule(self) -> None:
        self.assertEqual(self.bash('juno search "x kube-system namespace slack"'), "")
        self.assertEqual(self.bash('grep -rn "kube-system slack" src'), "")
        self.assertIn("staging context", self.bash("kubectl get pods -n kube-system"))

    def test_criterion_4_path_needs_a_whole_segment(self) -> None:
        self.write(
            "rules/topics/mantis.md", "triggers: mantis\n" + RULE.format("Mantis rule text here")
        )
        self.assertEqual(self.read_file("/x/mantis_brief.py"), "")
        self.assertEqual(self.bash("cat scripts/mantis_brief.py"), "")
        self.assertIn("Mantis rule", self.read_file("/x/mantis/notes.md"))
        self.reset()
        self.assertIn("Mantis rule", self.bash("cat scripts/mantis.py"))

    def test_criterion_5_one_generic_word_is_not_enough(self) -> None:
        self.assertEqual(self.prompt("how do I fix the kube thing"), "")
        self.assertIn("staging context", self.prompt("which kube context for deployments"))

    def test_criterion_5_specific_trigger_is_enough_alone(self) -> None:
        self.assertIn("staging context", self.prompt("look at the kube-system thing"))

    def test_generic_gate_applies_to_knowledge_too(self) -> None:
        self.write(
            "knowledge/queues.md",
            "---\ntitle: Queue facts\ntriggers: broker\n---\nMessages wait in the retry queue.\n",
        )
        self.assertEqual(self.prompt("the broker is fine"), "")
        self.assertIn("Queue facts", self.prompt("the broker retry queue is slow"))

    def test_pitfalls_keep_their_own_rule(self) -> None:
        self.write(
            "pitfalls.md",
            "- Always check x · triggers: sqlite3, x.db · ev; segment abcd1234 · 2026-01-01\n",
        )
        self.assertIn("Always check x", self.bash("sqlite3 data.db"))


class Doctor(Base):
    def doctor(self) -> str:
        return self.sb.cli("doctor", *self.sb.install_args())[1]

    def test_criterion_6_untriggered_knowledge_is_listed(self) -> None:
        self.write("knowledge/plain.md", "---\ntitle: Plain\nsources: [x]\n---\nbody text\n")
        self.write(
            "knowledge/ok.md", "---\ntitle: Ok\nsources: [x]\ntriggers: ok-thing\n---\nbody\n"
        )
        out = self.doctor()
        line = next(x for x in out.splitlines() if "knowledge/plain.md has no triggers" in x)
        self.assertIn("WARN", line)
        self.assertNotIn("knowledge/ok.md has no triggers", out)

    def test_criterion_6_most_fired_items_of_the_last_7_days(self) -> None:
        self.write(
            "knowledge/push.md",
            "---\ntitle: Push facts\nsources: [x]\ntriggers: git push\n---\nbody\n",
        )
        self.write("rules/topics/git.md", "triggers: git push\n" + RULE.format("Never force push"))
        for sid in (SID, OTHER_SID, "0b0b0b0b-1111-4222-8333-444455556666"):
            self.reset()
            self.hook(
                "pre_tool_use",
                self.payload(
                    "PreToolUse",
                    session_id=sid,
                    tool_name="Bash",
                    tool_input={"command": "git push origin x"},
                ),
            )
        old = os.path.join(self.sb.state, "topic-fires.jsonl")
        with open(old, "a", encoding="utf-8") as handle:
            stale = time.time() - 9 * 86400
            handle.write(json.dumps({"t": stale, "name": "knowledge-stale"}) + "\n")
        out = self.doctor()
        line = next(x for x in out.splitlines() if "most injected" in x)
        self.assertIn("topic-git (3)", line)
        self.assertIn("knowledge-push (3)", line)
        self.assertNotIn("knowledge-stale", line)

    def test_no_fires_no_line(self) -> None:
        self.assertNotIn("most injected", self.doctor())


if __name__ == "__main__":
    unittest.main()
