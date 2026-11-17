import os
from typing import Any

from juno import config
from juno.adapters import ledgerio
from tests.support.hooks import SID, HookCase

HEAD = "RULES (follow, pinned):"
TICKET = "ABC-12"
NOTE = "<task-notification>x</task-notification>"


def line(text: str, pinned: bool = False) -> str:
    return f'- 2026-11-17 · {text} · src: user abcd1234 · "q"' + (" · pinned" if pinned else "")


class Base(HookCase):
    def prompt(self, text: str = "hello there", pid: str = "p1", **extra: Any) -> str:
        payload = self.payload("UserPromptSubmit", prompt=text, prompt_id=pid, **extra)
        return self.context(self.hook("user_prompt_submit", payload))

    def pin(self, rel: str, *texts: str, plain: tuple[str, ...] = ()) -> None:
        head = "triggers: zebra-fish\n\n" if rel.startswith("rules/topics/") else ""
        rows = [line(t, True) for t in texts] + [line(t) for t in plain]
        self.write(rel, head + "\n".join(rows) + "\n")

    def cfg(self) -> config.Config:
        return config.load(self.env)

    def fired(self) -> list[str]:
        return [r["slug"] for r in self.ledger() if r.get("ev") == "topic_fired"]

    def touch_repo(self, name: str) -> None:
        path = self.make_repo(name)
        self.hook(
            "post_tool_use",
            self.payload(
                "PostToolUse",
                tool_name="Read",
                tool_input={"file_path": os.path.join(path, "a.py")},
                tool_response={},
            ),
        )

    def compact(self) -> None:
        self.hook("session_start", self.payload("SessionStart", source="compact"))


class Injection(Base):
    def test_1_real_prompt_has_block_first(self) -> None:
        self.pin("rules/global.md", "Reply in Turkish", plain=("Plain one",))
        text = self.prompt("that was wrong, never do that")
        self.assertTrue(text.startswith(HEAD + "\n- Reply in Turkish\n\n"), text)
        self.assertIn("lasting correction", text)
        self.assertNotIn("Plain one", text)
        self.assertNotIn("src:", text.split("\n\n")[0])

    def test_1_order_global_repo_topic(self) -> None:
        self.pin("rules/global.md", "G1", "G2")
        self.pin("rules/repos/alpha.md", "R1")
        self.pin("rules/topics/zeta.md", "T2")
        self.pin("rules/topics/beta.md", "T1")
        self.touch_repo("alpha")
        self.prompt("zebra-fish", pid="p0")
        self.assertEqual(self.fired(), ["beta", "zeta"])
        text = self.prompt("next", pid="p1")
        self.assertTrue(text.startswith(f"{HEAD}\n- G1\n- G2\n- R1\n- T1\n- T2"), text)

    def test_2_synthetic_prompts_get_only_the_block(self) -> None:
        self.pin("rules/global.md", "Reply in Turkish")
        shapes = (
            f"<task-notification>\n<task-id>x</task-id>\nBuild {TICKET} done\n</task-notification>",
            "[system notification] Monitor event: line",
            "<system-reminder>wake up</system-reminder>",
            "Another Claude session sent a message: hi",
            "[subagent hand-back] done",
            "<agent-message>hi</agent-message>",
        )
        for i, shape in enumerate(shapes):
            with self.subTest(shape):
                self.assertEqual(self.prompt(shape, pid=f"s{i}"), f"{HEAD}\n- Reply in Turkish")
        self.assertFalse([r for r in self.ledger() if r.get("ev") in ("prompt", "pack")])

    def test_3_subagent_gets_nothing(self) -> None:
        self.pin("rules/global.md", "Reply in Turkish")
        self.assertEqual(self.prompt("hi", agent_id="sub1"), "")
        self.assertEqual(self.prompt(NOTE, agent_id="sub1"), "")

    def test_4_no_pins_is_unchanged(self) -> None:
        self.write("rules/global.md", line("Plain") + "\n")
        payload = self.payload("UserPromptSubmit", prompt=NOTE)
        self.assertIsNone(self.hook("user_prompt_submit", payload))
        self.assertEqual(self.prompt("hello there"), "")
        self.assertEqual(
            self.prompt("[nosave]", pid="p2"),
            "Juno: [nosave] is set for this segment: nothing is written to the vault and no "
            "handoff card is made.",
        )
        self.assertEqual(
            self.prompt("that was wrong, never do that", pid="p3"),
            "If this is a lasting correction or preference, add it with `juno rule add` "
            "using my exact words.",
        )

    def test_5_long_pins_are_trimmed_with_marker(self) -> None:
        self.pin("rules/global.md", *[f"Rule number {i} " + "x" * 120 for i in range(12)])
        text = self.prompt("hello")
        cap = self.cfg().get_int("caps", "pinned_rules_chars")
        self.assertLessEqual(len(text), cap)
        self.assertTrue(text.startswith(HEAD))
        self.assertIn("Juno truncated", text)

    def test_6_packs_are_trimmed_not_the_block(self) -> None:
        self.pin("rules/global.md", "Reply in Turkish", "Never do that")
        self.write(
            f"tasks/{TICKET}.md",
            "# T\n" + "\n".join(f"- decision {i} " + "y" * 200 for i in range(80)) + "\n",
        )
        rows = [line(f"ticket rule {i} " + "z" * 150) for i in range(80)]
        self.write(f"rules/{TICKET}.md", "\n".join(rows) + "\n")
        text = self.prompt(f"work on {TICKET}")
        cap = self.cfg().get_int("caps", "hook_output_chars")
        self.assertLessEqual(len(text), cap)
        self.assertTrue(text.startswith(f"{HEAD}\n- Reply in Turkish\n- Never do that\n\n"), text)
        self.assertGreater(len(text), 1000)

    def test_12_nosave_does_not_suppress(self) -> None:
        self.pin("rules/global.md", "Reply in Turkish")
        self.assertIn(HEAD, self.prompt("[nosave]", pid="a"))
        self.assertIn(HEAD, self.prompt("more", pid="b"))
        self.assertIn(HEAD, self.prompt(NOTE, pid="c"))


class Activation(Base):
    def test_10_repo_pin_waits_for_the_repo(self) -> None:
        self.pin("rules/repos/alpha.md", "Repo rule")
        self.assertNotIn("Repo rule", self.prompt("hello", pid="a"))
        self.touch_repo("alpha")
        for pid in ("b", "c"):
            self.assertIn(f"{HEAD}\n- Repo rule", self.prompt("hello", pid=pid))

    def test_10_only_touched_repos_count(self) -> None:
        self.pin("rules/repos/alpha.md", "Repo rule")
        self.pin("rules/repos/beta.md", "Other rule")
        self.touch_repo("alpha")
        text = self.prompt("hello")
        self.assertIn("Repo rule", text)
        self.assertNotIn("Other rule", text)

    def test_11_repo_pin_survives_compaction(self) -> None:
        self.pin("rules/repos/alpha.md", "Repo rule")
        self.touch_repo("alpha")
        self.compact()
        self.assertEqual(ledgerio.fold(self.cfg(), SID).packs, {})
        self.assertIn("Repo rule", self.prompt("after compact"))

    def test_topic_pin_waits_for_the_topic_and_survives_compaction(self) -> None:
        self.pin("rules/topics/fish.md", "Topic rule")
        self.assertNotIn("Topic rule", self.prompt("hello", pid="a"))
        self.prompt("zebra-fish is here", pid="b")
        self.assertEqual(self.fired(), ["fish"])
        self.assertIn("Topic rule", self.prompt("hello", pid="c"))
        self.compact()
        self.assertEqual(os.listdir(os.path.join(self.sb.state, "topics")), [])
        self.assertIn("Topic rule", self.prompt("hello", pid="d"))
        self.assertIn("Topic rule", self.prompt(NOTE, pid="e"))

    def test_subagent_topic_fire_is_not_recorded(self) -> None:
        self.pin("rules/topics/fish.md", "Topic rule")
        self.prompt("zebra-fish", agent_id="sub1")
        self.assertEqual(self.fired(), [])

    def test_ledger_without_the_event_folds(self) -> None:
        self.assertEqual(ledgerio.fold(self.cfg(), SID).topics_fired, set())
