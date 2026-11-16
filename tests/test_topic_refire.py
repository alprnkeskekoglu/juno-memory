"""Issue #36 part A: a claimed pitfall re-fires only on a repeat of its error, once per segment."""

import json
import os
import re
import unittest
from typing import Any

from juno.core import fold, topics
from tests.test_topics import Base

PY_EVIDENCE = "python3 x/state.py batch failed: invalid stage"
ENTRY: list[Any] = [
    "pitfall-aaaa1111",
    "pitfalls.md",
    ["python3", "state.py"],
    "Pass a valid stage to the batch step",
    PY_EVIDENCE + "; segment abcd1234",
]
LEDGER = os.path.join(os.path.dirname(__file__), "fixtures", "refire_ledger.jsonl")


class Repeats(unittest.TestCase):
    def test_interpreter_and_command_tokens_do_not_refire(self) -> None:
        for text in ("python3 other.py", "python3 x/state.py batch", "python3 -m pytest"):
            self.assertFalse(topics.repeats(ENTRY, text), text)

    def test_repeated_error_phrase_refires(self) -> None:
        self.assertTrue(topics.repeats(ENTRY, "python3 other.py --note Invalid  Stage"))

    def test_token_from_the_error_part_refires(self) -> None:
        entry = [*ENTRY[:4], "bash run.sh failed: missing_manifest.json not found; segment x"]
        self.assertTrue(topics.repeats(entry, "cat missing_manifest.json"))
        self.assertFalse(topics.repeats(entry, "bash run.sh"))

    def test_tokens_of_the_fix_text_do_not_refire(self) -> None:
        entry = [
            "pitfall-bbbb2222",
            "pitfalls.md",
            ["x"],
            "Run scripts/check_all.sh before pushing",
            "push failed: scripts/check_all.sh was skipped",
        ]
        self.assertFalse(topics.repeats(entry, "scripts/check_all.sh"))

    def test_evidence_tokens_drop_executables(self) -> None:
        self.assertNotIn("python3", topics.evidence_tokens("python3 failed: bad_thing.py gone"))


class HookRefire(Base):
    def setUp(self) -> None:
        super().setUp()
        line = (
            f"- Pass a valid stage to the batch step · triggers: python3, state.py · "
            f"{PY_EVIDENCE}; segment abcd1234 · 2026-01-01\n"
        )
        self.write("pitfalls.md", "# Pitfalls\n" + line)

    def count(self, command: str) -> int:
        return self.context(self.bash(command)).count("[Pitfall · pitfalls.md]")

    def test_criterion_1_refires_only_on_the_repeated_error_and_once(self) -> None:
        self.assertEqual(self.count("python3 x/state.py batch"), 1)
        self.assertEqual(self.count("python3 other.py"), 0)
        self.assertEqual(self.count("python3 other.py --note invalid stage"), 1)
        self.assertEqual(self.count("python3 other.py --note invalid stage"), 0)


def legacy_repeats(entry: list[Any], text: str) -> bool:
    """The pre-#36 rule: any long token with a punctuation or digit in the evidence."""
    tail = re.compile(r";\s*segment\b.*$", re.I)
    haystack = fold.fold(text)
    tokens = [
        t.strip("./-")
        for t in re.findall(r"[\w./-]+", fold.fold(tail.sub("", entry[4])))
        if len(t.strip("./-")) >= 6 and re.search(r"[_./\d-]", t.strip("./-"))
    ]
    return any(t in haystack and topics.pattern(t).search(haystack) for t in tokens)


class StubView:
    def hidden(self, rel: str, text: str | None = None) -> bool:
        return False


def pitfall(n: int, triggers: list[str], evidence: str) -> list[Any]:
    return [f"pitfall-0000000{n}", "pitfalls.md", triggers, f"Lesson number {n}", evidence]


REPLAY_ENTRIES = [
    pitfall(1, ["python3", "tool_1.py"], "python3 scripts/tool_1.py --run 3 failed: invalid stage"),
    pitfall(2, ["bash", "deploy_helper.sh"], "bash deploy_helper.sh stage1: missing_manifest.json"),
    pitfall(3, ["node", "step1.js"], "node build/step1.js crashed: cannot find module"),
]


class Replay(unittest.TestCase):
    """Criterion 2: replay of a synthetic recorded session (tests/fixtures/refire_ledger.jsonl)."""

    def calls(self) -> list[str]:
        with open(LEDGER, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle]
        return [r["command"] for r in rows if r.get("ev") == "tool"]

    def test_refires_drop_by_90_percent_and_no_first_injection_is_lost(self) -> None:
        claimed: set[str] = set()
        legacy_first: set[str] = set()
        legacy_refires = new_refires = 0
        new_first: set[str] = set()
        for command in self.calls():
            text, execs, paths = topics.tool_parts("Bash", {"command": command})
            hits = topics.matches(REPLAY_ENTRIES, text, "none", execs, paths)
            seen = text + " " + " ".join(paths)
            for hit in hits:
                if hit[0] in legacy_first and legacy_repeats(hit, seen):
                    legacy_refires += 1
                legacy_first.add(hit[0])
            again = frozenset(h[0] for h in hits if topics.repeats(h, seen))

            def claim(name: str) -> bool:
                if name in claimed:
                    return False
                claimed.add(name)
                return True

            shown = topics.render(StubView(), hits, claim, 10**6, 0, again)  # type: ignore[arg-type]
            for hit in hits:
                if hit[3] in shown:
                    if hit[0] in new_first:
                        new_refires += 1
                    new_first.add(hit[0])
        self.assertGreaterEqual(legacy_refires, 50)
        self.assertLessEqual(new_refires, legacy_refires // 10)
        self.assertEqual(new_first, legacy_first)


if __name__ == "__main__":
    unittest.main()
