import os
import unittest
from typing import Any

from tests.support.hooks import HookCase

RULE = '- 2026-01-01 · {} · src: user abcd1234 · "quote"\n'


class VaultEditRefusal(HookCase):
    def call(self, tool: str, path: str, **extra: Any) -> dict[str, Any] | None:
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        payload = self.payload("PreToolUse", tool_name=tool, tool_input={key: path}, **extra)
        return self.hook("pre_tool_use", payload)

    def reason(self, out: dict[str, Any] | None) -> str:
        assert out is not None
        decision = out["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        text: str = decision["permissionDecisionReason"]
        self.assertEqual(len(text.splitlines()), 1)
        return text

    def test_every_write_tool_is_refused_with_a_verb_pointer(self) -> None:
        for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            text = self.reason(self.call(tool, self.vp("notes/a.md")))
            self.assertIn("juno note", text, tool)

    def test_pointer_follows_the_file_kind(self) -> None:
        expected = {
            "handoffs/app.md": "juno card",
            "knowledge/q.md": "juno knowledge add",
            "decisions/s.md": "juno decision",
            "projects/app.md": "juno project",
            "procedures/r.md": "juno procedure",
            "pitfalls.md": "juno pitfall",
            "threads.md": "juno thread add",
            "ideas/i.md": "juno idea",
            "inbox.md": "juno capture",
            "self/values.md": "juno self",
            "rules/global.md": "juno rule",
            "backlog/x.md": "juno backlog",
            "core.md": "a juno verb",
        }
        for rel, verb in expected.items():
            self.assertIn(verb, self.reason(self.call("Edit", self.vp(rel))), rel)

    def test_new_file_and_relative_path(self) -> None:
        self.assertIn("juno note", self.reason(self.call("Write", self.vp("notes/new.md"))))
        out = self.hook(
            "pre_tool_use",
            self.payload(
                "PreToolUse",
                tool_name="Write",
                cwd=self.sb.vault,
                tool_input={"file_path": "notes/../notes/x.md"},
            ),
        )
        self.assertIn("juno note", self.reason(out))

    def test_symlink_into_the_vault_is_refused(self) -> None:
        link = os.path.join(self.sb.root, "link-to-vault")
        os.symlink(self.sb.vault, link)
        self.reason(self.call("Edit", os.path.join(link, "notes", "a.md")))
        os.symlink(self.vp("notes"), os.path.join(self.work, "n"))
        self.reason(self.call("Write", os.path.join(self.work, "n", "a.md")))

    def test_symlinked_vault_location(self) -> None:
        real = os.path.join(self.sb.root, "real-vault")
        os.rename(self.sb.vault, real)
        os.symlink(real, self.sb.vault)
        self.reason(self.call("Edit", os.path.join(real, "notes", "a.md")))
        self.reason(self.call("Edit", os.path.join(self.sb.vault, "notes", "a.md")))

    def test_dotdot_into_the_vault_is_refused(self) -> None:
        path = os.path.join(self.work, "..", "vault", "notes", "a.md")
        self.reason(self.call("Edit", path))

    def test_other_paths_pass(self) -> None:
        sibling = self.sb.vault + "-other"
        os.makedirs(sibling)
        for path in (
            os.path.join(self.work, "a.txt"),
            os.path.join(sibling, "a.md"),
            os.path.join(self.sb.state, "x.json"),
        ):
            self.assertIsNone(self.call("Write", path), path)

    def test_a_symlink_inside_the_vault_pointing_out_edits_the_outside_file(self) -> None:
        os.symlink(self.work, self.vp("notes/out"))
        self.assertIsNone(self.call("Write", self.vp("notes/out/a.txt")))

    def test_subagent_edit_is_refused_too(self) -> None:
        self.reason(self.call("Edit", self.vp("notes/a.md"), agent_id="ag1"))

    def test_headless_session_is_not_touched(self) -> None:
        env = {"JUNO_TEST_INTERACTIVE": ""}
        payload = self.payload(
            "PreToolUse", tool_name="Edit", tool_input={"file_path": self.vp("notes/a.md")}
        )
        self.assertIsNone(self.hook("pre_tool_use", payload, env=env))

    def test_missing_path_does_nothing(self) -> None:
        payload = self.payload("PreToolUse", tool_name="Edit", tool_input={})
        self.assertIsNone(self.hook("pre_tool_use", payload))

    def test_juno_state_is_refused(self) -> None:
        for rel in (
            "ledgers/57fb7685.jsonl",
            "cards/x.job.json",
            "agent-calls/s.t",
            "writes.jsonl",
            "writes.jsonl.1",
            "Ledgers/a.jsonl",
        ):
            text = self.reason(self.call("Write", os.path.join(self.sb.state, rel)))
            self.assertIn("own state", text, rel)

    def test_state_through_a_symlink_is_refused(self) -> None:
        link = os.path.join(self.work, "led")
        os.symlink(os.path.join(self.sb.state, "ledgers"), link)
        self.reason(self.call("Edit", os.path.join(link, "x.jsonl")))

    def test_non_vault_edits_still_get_topic_triggers(self) -> None:
        self.write("rules/topics/docs.md", "triggers: runbook\n" + RULE.format("Docs rule"))
        out = self.call("Edit", os.path.join(self.work, "runbook.md"))
        self.assertIn("Docs rule", self.context(out))
        self.assertNotIn("permissionDecision", str(out))


if __name__ == "__main__":
    unittest.main()
