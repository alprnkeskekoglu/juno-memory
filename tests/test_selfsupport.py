import json
import unittest
from typing import Any

from juno.adapters import learnio
from juno.core import selfmodel, writegate
from tests.support.cardcase import CardCase
from tests.support.write import WriteCase

Q1 = "I would rather ship the small fix today and refactor the module next week"
Q2 = "again: ship the small fix first, the refactor can wait until next week"
Q3 = "no, do the full refactor now, the small fix only hides the real problem"
TEXT = "Prefers the small fix now over the refactor when time is short"
TEXT2 = "Prefers the small fix now over the refactor when time is short today"
NEW = "Wants the full refactor before any small fix is shipped"


def line(text: str, day: str, *tail: str, quote: str = Q1) -> str:
    return " · ".join([f"- {text}", day, "src: user abcd1234", f'"{quote}"', *tail])


class Parse(unittest.TestCase):
    def test_old_format_is_seen_once_last_is_its_date(self) -> None:
        entry = selfmodel.parse(line(TEXT, "2026-11-01"))
        self.assertEqual((entry.seen, entry.last, entry.superseded), (1, "2026-11-01", ""))
        self.assertEqual(entry.text, TEXT)

    def test_new_fields_parse(self) -> None:
        entry = selfmodel.parse(
            line(TEXT, "2026-11-01", "seen 3×", "last 2026-11-20", "superseded 2026-11-22")
        )
        self.assertEqual(
            (entry.seen, entry.last, entry.superseded), (3, "2026-11-20", "2026-11-22")
        )

    def test_bump_keeps_date_and_quote(self) -> None:
        once = selfmodel.bump(line(TEXT, "2026-11-01"), "2026-11-10")
        self.assertEqual(once, line(TEXT, "2026-11-01", "seen 2×", "last 2026-11-10"))
        twice = selfmodel.bump(once, "2026-11-20")
        self.assertEqual(twice, line(TEXT, "2026-11-01", "seen 3×", "last 2026-11-20"))

    def test_mark_superseded_appends_last(self) -> None:
        row = line(TEXT, "2026-11-01", "seen 2×", "last 2026-11-10")
        self.assertEqual(
            selfmodel.mark_superseded(row, "2026-11-20"), row + " · superseded 2026-11-20"
        )

    def test_core_text_ignores_the_new_fields(self) -> None:
        row = line(TEXT, "2026-11-01", "seen 3×", "last 2026-11-20")
        self.assertEqual(writegate.core_text(row), TEXT)


class Block(unittest.TestCase):
    def test_superseded_lines_are_not_injected(self) -> None:
        files = [
            line("Gone one", "2026-11-01", "superseded 2026-11-05")
            + "\n"
            + line("Kept", "2026-10-01")
        ]
        text = selfmodel.block(files, 12, 1200)
        self.assertIn("Kept", text)
        self.assertNotIn("Gone one", text)
        self.assertNotIn("more in self/", text)

    def test_rank_is_support_then_last_seen(self) -> None:
        names = ("Old but often", "Twice recent", "Twice older", "Fresh once")
        files = [
            "\n".join(
                [
                    line(names[3], "2026-11-09"),
                    line(names[0], "2026-09-01", "seen 4×", "last 2026-09-02"),
                    line(names[2], "2026-10-02", "seen 2×", "last 2026-10-05"),
                    line(names[1], "2026-10-01", "seen 2×", "last 2026-11-08"),
                ]
            )
        ]
        text = selfmodel.block(files, 12, 1200)
        self.assertEqual(sorted(names, key=text.index), list(names))

    def test_whole_lines_only_and_a_hint_for_the_hidden(self) -> None:
        rows = [line(f"Observation number {n}", f"2026-11-{n + 1:02d}") for n in range(6)]
        text = selfmodel.block(["\n".join(rows)], 3, 1200)
        self.assertEqual(text.count("\n- "), 3)
        self.assertTrue(text.rstrip().endswith("(+3 more in self/)"))
        budget = len(selfmodel.HEADER) + 330
        small = selfmodel.block(["\n".join(rows)], 12, budget)
        self.assertLessEqual(len(small), budget)
        for kept in small.splitlines():
            if kept.startswith("- "):
                self.assertIn('"', kept[-1:], kept)
        self.assertIn("more in self/)", small)

    def test_no_hint_when_nothing_was_left_out(self) -> None:
        text = selfmodel.block([line("Only one", "2026-11-01")], 12, 1200)
        self.assertNotIn("more in self/", text)


class Summary(unittest.TestCase):
    def test_counts(self) -> None:
        files = [
            line("A", "2026-11-01", "seen 3×", "last 2026-11-02")
            + "\n"
            + line("B", "2026-11-01", "superseded 2026-11-03")
            + "\n"
            + line("C", "2026-11-01", "seen 7×", "last 2026-11-02", "superseded 2026-11-03"),
            line("D", "2026-11-01"),
        ]
        self.assertEqual(selfmodel.summary(files), (4, 2, 3))
        self.assertEqual(
            selfmodel.summary_text(files), "4 lines (2 superseded), top line seen 3×"
        )
        self.assertEqual(selfmodel.summary(["# Values\n"]), (0, 0, 0))


class Learner(CardCase):
    def learn(self, extract: dict[str, object], *prompts: str) -> dict[str, Any]:
        self.dirty(" . ".join(prompts))
        result, _ = learnio.apply(self.cfg(), self.seg(), json.dumps(extract), "digest", [])
        return result["self"]

    def entry(self, text: str, quote: str, **over: object) -> dict[str, object]:
        return {"file": "values", "text": text, "quote": quote, **over}

    def rows(self) -> list[str]:
        return [x for x in self.read("self/values.md").splitlines() if x.startswith("- ")]

    def test_near_duplicate_bumps_instead_of_dropping(self) -> None:
        self.learn({"self": [self.entry(TEXT, Q1)]}, Q1)
        out = self.learn({"self": [self.entry(TEXT2, Q2)]}, Q2)
        self.assertEqual(out["dropped"], {})
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(selfmodel.parse(rows[0]).seen, 2)
        self.assertIn(f'"{Q1}"', rows[0])
        self.assertNotIn(Q2, rows[0])

    def test_same_evidence_again_does_not_inflate(self) -> None:
        self.learn({"self": [self.entry(TEXT, Q1)]}, Q1)
        out = self.learn({"self": [self.entry(TEXT, Q1)]}, Q1)
        self.assertEqual(out["dropped"], {"duplicate": 1})
        self.assertNotIn("seen 2", self.read("self/values.md"))

    def test_supersedes_marks_the_old_line_and_adds_the_new(self) -> None:
        self.learn({"self": [self.entry(TEXT, Q1)]}, Q1)
        out = self.learn({"self": [self.entry(NEW, Q3, supersedes=TEXT)]}, Q3)
        self.assertEqual(out["written"], ["values"])
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertRegex(rows[0], r" · superseded \d{4}-\d{2}-\d{2}$")
        self.assertNotIn("superseded", rows[1])
        self.assertTrue(rows[1].startswith("- " + NEW))

    def test_no_supersede_by_similarity_or_for_an_unknown_line(self) -> None:
        self.learn({"self": [self.entry(TEXT, Q1)]}, Q1)
        self.learn({"self": [self.entry(NEW, Q3, supersedes="Some line that is not there")]}, Q3)
        self.learn({"self": [self.entry("Wants the refactor now over the small fix", Q3)]}, Q3)
        self.assertNotIn("superseded", self.read("self/values.md"))

    def test_instruction_mentions_supersedes(self) -> None:
        text = "\n".join(learnio.instructions(self.cfg(), self.seg()))
        self.assertIn('"supersedes"', text)


class Cli(WriteCase):
    def say(self, *argv: str) -> str:
        code, out = self.juno("self", "--file", "values", *argv)
        self.assertEqual(code, 0, out)
        return out

    def rows(self) -> list[str]:
        return [x for x in self.get("self/values.md").splitlines() if x.startswith("- ")]

    def test_near_duplicate_bumps(self) -> None:
        self.prompt(Q1, "pid00002")
        self.prompt(Q2, "pid00003")
        self.say("--evidence", Q1, TEXT)
        out = self.say("--evidence", Q2, TEXT2)
        self.assertIn("revert with `juno undo", out)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(selfmodel.parse(self.rows()[0]).seen, 2)
        self.assertIn(f'"{Q1}"', self.rows()[0])
        self.assertIn("already recorded", self.say("--evidence", Q1, TEXT2))

    def test_supersedes_marks_old_and_is_undoable(self) -> None:
        self.prompt(Q1, "pid00002")
        self.prompt(Q3, "pid00003")
        self.say("--evidence", Q1, TEXT)
        before = self.get("self/values.md")
        out = self.say(
            "--supersedes", "self/values.md:2", "--was", "small fix now", "--evidence", Q3, NEW
        )
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertRegex(rows[0], r" · superseded \d{4}-\d{2}-\d{2}$")
        wid = out.split("(write ")[1].split(";")[0]
        code, _ = self.juno("undo", wid)
        self.assertEqual(code, 0)
        self.assertEqual(self.get("self/values.md"), before)

    def test_supersedes_needs_was_and_a_matching_line(self) -> None:
        self.prompt(Q1, "pid00002")
        self.prompt(Q3, "pid00003")
        self.say("--evidence", Q1, TEXT)
        for extra in (
            ["--supersedes", "self/values.md:2"],
            ["--supersedes", "self/values.md:2", "--was", "nothing like it"],
            ["--supersedes", "self/decisions.md:2", "--was", "small fix now"],
            ["--supersedes", "self/values.md:1", "--was", "Values"],
        ):
            code, _ = self.juno("self", "--file", "values", *extra, "--evidence", Q3, NEW)
            self.assertNotEqual(code, 0, extra)
        self.assertEqual(len(self.rows()), 1)

    def test_cli_line_keeps_a_short_prompt_id(self) -> None:
        # A full uuid prompt id trips the privacy id check; the learner already keeps 8 chars.
        self.prompt(Q1, "26224046-2117-415c-b88a-797b951c5b52")
        self.say("--evidence", Q1, TEXT)
        self.assertIn("src: user 26224046 ·", self.rows()[0])

    def test_doctor_line(self) -> None:
        self.prompt(Q1, "pid00002")
        self.prompt(Q2, "pid00003")
        self.say("--evidence", Q1, TEXT)
        self.say("--evidence", Q2, TEXT2)
        _, out = self.juno("doctor", "--json", *self.sb.install_args())
        found = [c for c in json.loads(out)["checks"] if c["name"] == "self"]
        self.assertEqual(
            [(c["status"], c["detail"]) for c in found],
            [("INFO", "1 lines (0 superseded), top line seen 2×")],
        )


if __name__ == "__main__":
    unittest.main()
