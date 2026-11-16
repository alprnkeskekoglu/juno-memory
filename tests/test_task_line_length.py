import os
import re

from juno.core import packs
from juno.core.packs import Caps
from tests.support import REPO
from tests.test_packs import FakeVault
from tests.test_task_log import QUOTE, LogCase

WORDS = "alpha bravo charlie delta echo foxtrot "


def long_line(size: int) -> str:
    text = (WORDS * (size // len(WORDS) + 1))[:size]
    return text[:-1] + "x" if text.endswith(" ") else text


class TaskLineLength(LogCase):
    def test_900_characters_are_accepted(self) -> None:
        text = long_line(900)
        code, out = self.one("Plan", "summary", text)
        self.assertEqual(code, 0, out)
        self.assertIn(f"- [summary] {text} · ", self.task())

    def test_1000_are_accepted_and_1001_refused_naming_the_limit(self) -> None:
        self.assertEqual(self.one("Plan", "a", long_line(1000))[0], 0)
        code, out = self.one("Plan", "b", long_line(1001))
        self.assertEqual(code, 2)
        self.assertIn("8-1000 characters", out)

    def test_below_the_minimum_is_still_refused(self) -> None:
        code, out = self.one("Plan", "a", "short")
        self.assertEqual(code, 2)
        self.assertIn("8-1000 characters", out)

    def test_one_long_row_refuses_the_whole_batch(self) -> None:
        rows = [
            {"section": "Plan", "key": "a", "line": "a perfectly fine line"},
            {"section": "Plan", "key": "b", "line": long_line(1001)},
        ]
        code, out = self.log("--batch-file", self.batch(rows))
        self.assertEqual(code, 2)
        self.assertIn("row 2", out)
        self.assertFalse(os.path.exists(self.vp("tasks/ABC-100.md")))

    def test_knowledge_add_still_refuses_301_characters(self) -> None:
        args = ("knowledge", "add", "--topic", "queues", "--evidence", QUOTE)
        code, out = self.juno(*args, long_line(301))
        self.assertEqual(code, 2)
        self.assertIn("8-300 characters", out)
        self.assertEqual(self.juno(*args, long_line(300))[0], 0)


def pack(line: str, chars: int) -> str:
    doc = (
        "---\njira: ABC-100\ntitle: T\nstatus: open\ndigests: [Plan]\n---\n\n"
        f"## Plan\n- [summary] {line} · 2026-11-03\n"
    )
    view = FakeVault({"tasks/ABC-100.md": doc})
    return packs.render_pack(view, Caps(pack_chars=chars), "ticket", "ABC-100", [])


class PackCut(LogCase):
    def test_a_900_character_line_within_budget_shows_whole(self) -> None:
        line = long_line(900)
        self.assertIn(f"- [summary] {line} · 2026-11-03", pack(line, 4500))

    def test_over_budget_the_line_is_cut_at_a_word_boundary_not_dropped(self) -> None:
        line = long_line(900)
        text = pack(line, 500)
        self.assertLessEqual(len(text), 500)
        shown = re.search(r"- \[summary\] (.*) …", text)
        self.assertIsNotNone(shown, text)
        assert shown
        cut = shown.group(1)
        self.assertTrue(line.startswith(cut))
        self.assertIn(line[len(cut) : len(cut) + 1], (" ", ""))
        self.assertTrue(cut.endswith(tuple(WORDS.split())))


class SpecRow(LogCase):
    def test_task_log_row_states_the_limits_and_whole_batch_refusal(self) -> None:
        with open(os.path.join(REPO, "docs", "spec.md"), encoding="utf-8") as handle:
            row = next(line for line in handle if line.startswith("| `task log KEY"))
        self.assertIn("8-1000 characters", row)
        self.assertIn("refuses the whole batch", row)
