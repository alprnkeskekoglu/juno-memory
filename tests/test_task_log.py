import json
import os
import re
import time

from juno.adapters import writelog
from tests.support.write import WriteCase

QUOTE = "hello there, this is the first typed prompt"
TODAY = time.strftime("%Y-%m-%d")
HAND = (
    "---\njira: ABC-100\ntitle: Fix the thing\nstatus: open\nrepos: [alpha, beta]\n"
    "weird_field: keep me\ncreated: 2026-01-01\nupdated: 2026-01-02\n---\n\n"
    "## Decisions\n- decided it (user pid00001, 2026-01-02)\n\n## Notes\n- a note\n- [x] not a key\n"
)


class LogCase(WriteCase):
    TICKETS = True

    def log(
        self, *argv: str, key: str = "ABC-100", evidence: str | None = QUOTE
    ) -> tuple[int, str]:
        tail = () if evidence is None else ("--evidence", evidence)
        return self.juno("task", "log", key, *argv, *tail)

    def one(self, section: str, key: str, line: str, **kw: str | None) -> tuple[int, str]:
        return self.log("--section", section, f"--key={key}", line, **kw)  # type: ignore[arg-type]

    def batch(self, rows: list[object] | str, name: str = "rows.jsonl") -> str:
        text = rows if isinstance(rows, str) else "\n".join(json.dumps(r) for r in rows) + "\n"
        return self.body_file(text, name)

    def wid(self, out: str) -> str:
        match = re.search(r"write ([\w-]+);", out)
        assert match, out
        return match.group(1)

    def task(self) -> str:
        return self.get("tasks/ABC-100.md")

    def writes(self) -> int:
        return len(writelog.read(self.cfg))


class SingleLine(LogCase):
    def test_same_section_and_key_leaves_one_line(self) -> None:
        self.put("tasks/ABC-100.md", HAND)
        self.assertEqual(self.one("Plan", "step1", "first wording of it")[0], 0)
        code, out = self.one("Plan", "step1", "second wording of it")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.task().count("[step1]"), 1)
        self.assertIn(f"- [step1] second wording of it · {TODAY}", self.task())
        self.assertNotIn("first wording", self.task())

    def test_different_key_appends_and_replace_keeps_position(self) -> None:
        self.put("tasks/ABC-100.md", HAND)
        for key, text in (
            ("a", "alpha text here"),
            ("b", "bravo text here"),
            ("c", "charlie text"),
        ):
            self.assertEqual(self.one("Plan", key, text)[0], 0)
        self.assertEqual(self.one("Plan", "b", "bravo rewritten")[0], 0)
        body = self.task().split("## Plan\n")[1].rstrip("\n").split("\n")
        self.assertEqual(
            body,
            [
                f"- [a] alpha text here · {TODAY}",
                f"- [b] bravo rewritten · {TODAY}",
                f"- [c] charlie text · {TODAY}",
            ],
        )

    def test_digests_list_and_untouched_bytes(self) -> None:
        self.put("tasks/ABC-100.md", HAND)
        self.assertEqual(self.one("Plan", "a", "alpha text here")[0], 0)
        self.assertEqual(self.one("QA", "run", "qa ran green")[0], 0)
        self.assertEqual(self.one("Plan", "b", "bravo text here")[0], 0)
        text = self.task()
        self.assertIn("\ndigests: [Plan, QA]\n", text)
        front, _, body = text.partition("\n---\n\n")
        expected_front = HAND.split("\n---\n\n")[0].replace(
            "updated: 2026-01-02", f"updated: {TODAY}"
        )
        self.assertEqual(front, expected_front + "\ndigests: [Plan, QA]")
        old_body = HAND.split("\n---\n\n")[1]
        self.assertTrue(body.startswith(old_body), body)
        self.assertEqual(body[len(old_body) :].count("## "), 2)

    def test_missing_file_is_created_from_the_stub_in_one_commit(self) -> None:
        self.assertEqual(self.get("tasks/ABC-100.md"), "")
        code, out = self.one("Plan", "a", "alpha text here")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.writes(), 1)
        text = self.task()
        self.assertTrue(text.startswith("---\njira: ABC-100\n"))
        self.assertIn(f"created: {TODAY}\nupdated: {TODAY}\ndigests: [Plan]\n---\n", text)
        self.assertLess(text.index("## Decisions"), text.index("## Notes"))
        self.assertLess(text.index("## Notes"), text.index("## Plan"))
        self.assertTrue(text.endswith(f"## Plan\n- [a] alpha text here · {TODAY}\n"))
        wid = self.wid(out)
        self.assertEqual(self.juno("undo", wid)[0], 0)
        self.assertFalse(os.path.exists(self.vp("tasks/ABC-100.md")))

    def test_new_file_gets_the_default_url(self) -> None:
        with open(self.sb.config, encoding="utf-8") as handle:
            text = handle.read()
        with open(self.sb.config, "w", encoding="utf-8") as handle:
            handle.write(text.replace("[jira]", '[jira]\nbase_url = "https://jira.example.test"'))
        self.one("Plan", "a", "alpha text here")
        self.assertIn("url: https://jira.example.test/browse/ABC-100", self.task())

    def test_unchanged_result_writes_nothing(self) -> None:
        self.put("tasks/ABC-100.md", HAND)
        self.one("Plan", "a", "alpha text here")
        before = self.writes()
        code, out = self.one("Plan", "a", "alpha text here")
        self.assertEqual(code, 0, out)
        self.assertIn("unchanged", out)
        self.assertEqual(self.writes(), before)

    def test_output_line(self) -> None:
        out = self.one("Plan", "a", "alpha text here")[1]
        self.assertRegex(
            out,
            r"Juno: task ABC-100: logged 1 line\(s\) in tasks/ABC-100\.md \(1 added, 0 replaced; write [\w-]+; "
            r"revert with `juno undo [\w-]+`\)",
        )


class Batch(LogCase):
    ROWS = [
        {"section": "Plan", "key": f"k{n}", "line": f"line number {n} of plan"} for n in range(4)
    ] + [{"section": "QA", "key": "run", "line": "qa ran green"}]

    def test_five_rows_are_one_write_and_undo_restores_the_bytes(self) -> None:
        self.put("tasks/ABC-100.md", HAND)
        code, out = self.log("--batch-file", self.batch(self.ROWS))
        self.assertEqual(code, 0, out)
        self.assertIn("logged 5 line(s)", out)
        self.assertEqual(self.writes(), 1)
        (rec,) = writelog.read(self.cfg)
        self.assertEqual(rec["verb"], "task-log")
        self.assertEqual(self.task().count(" · " + TODAY), 5)
        self.assertEqual(self.juno("undo", self.wid(out))[0], 0)
        self.assertEqual(self.task(), HAND)

    def test_batch_that_creates_the_file_undoes_to_nothing(self) -> None:
        code, out = self.log("--batch-file", self.batch(self.ROWS))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.juno("undo", self.wid(out))[0], 0)
        self.assertFalse(os.path.exists(self.vp("tasks/ABC-100.md")))

    def test_the_file_is_unlinked_after_reading(self) -> None:
        path = self.batch(self.ROWS)
        self.log("--batch-file", path)
        self.assertFalse(os.path.exists(path))

    def refused(self, rows: list[object] | str, needle: str) -> None:
        self.put("tasks/ABC-100.md", HAND)
        code, out = self.log("--batch-file", self.batch(rows))
        self.assertEqual(code, 2, out)
        self.assertIn(needle, out)
        self.assertEqual(self.task(), HAND)
        self.assertEqual(self.writes(), 0)

    def test_duplicate_pair_is_refused(self) -> None:
        self.refused([self.ROWS[0], self.ROWS[1], dict(self.ROWS[0], line="again again")], "row 3")

    def test_bad_json_names_the_row(self) -> None:
        text = json.dumps(self.ROWS[0]) + "\n{not json}\n"
        self.refused(text, "row 2")

    def test_blank_line_non_object_unknown_field_and_bad_row_name_the_row(self) -> None:
        good = json.dumps(self.ROWS[0])
        self.refused(good + "\n\n", "row 2")
        self.refused(good + "\n[1]\n", "row 2")
        self.refused([self.ROWS[0], dict(self.ROWS[1], extra="x")], "row 2")
        self.refused([self.ROWS[0], dict(self.ROWS[1], section="Notes")], "row 2")
        self.refused([self.ROWS[0], dict(self.ROWS[1], line="ref 123456789012 failed")], "row 2")

    def test_over_the_row_and_size_limits(self) -> None:
        rows = [{"section": "Plan", "key": f"k{n}", "line": "line of plan text"} for n in range(41)]
        self.refused(rows, "40")
        self.put("tasks/ABC-100.md", HAND)
        path = self.batch("x" * 17000)
        code, out = self.log("--batch-file", path)
        self.assertEqual(code, 2, out)
        self.assertIn("--batch-file is over", out)
        self.assertEqual(self.task(), HAND)

    def test_cannot_mix_with_the_single_form(self) -> None:
        path = self.batch(self.ROWS)
        code, out = self.log(
            "--batch-file", path, "--section", "Plan", "--key", "a", "text here ok"
        )
        self.assertEqual(code, 2, out)
        self.assertEqual(self.writes(), 0)

    def test_file_outside_tmp_and_state_is_refused(self) -> None:
        code, out = self.log("--batch-file", os.path.join(self.sb.vault, "tasks", "x.jsonl"))
        self.assertEqual(code, 2, out)


class Validation(LogCase):
    def refused(self, section: str, key: str, line: str, needle: str = "") -> None:
        before = self.get("tasks/ABC-100.md")
        code, out = self.one(section, key, line)
        self.assertEqual(code, 2, out)
        self.assertIn("refused", out)
        self.assertIn(needle, out)
        self.assertEqual(self.get("tasks/ABC-100.md"), before)
        self.assertEqual(self.writes(), 0)

    def test_section_rules(self) -> None:
        for bad in ("Notes", "decisions", "LINKS", "plan", "X", "Bad_name", "A" * 31, "Two\nlines"):
            self.refused(bad, "a", "some text here", "section")

    def test_key_rules(self) -> None:
        for bad in ("Upper", "-lead", "a b", "k" * 41, ""):
            self.refused("Plan", bad, "some text here", "key")

    def test_text_rules(self) -> None:
        self.refused("Plan", "a", "ref 123456789012 failed", "")
        self.refused("Plan", "a", "# heading like", "")
        self.refused("Plan", "a", "[tag] looks like a key", "")
        self.refused("Plan", "a", "two\nlines of text", "one line")
        self.refused("Plan", "a", "has a · dot in it", "")
        self.refused("Plan", "a", "short", "")

    def test_flagged_id_is_refused_with_the_reason_and_not_masked(self) -> None:
        code, out = self.one("Plan", "a", "ref 123456789012 failed")
        self.assertEqual(code, 2, out)
        self.assertNotIn("123456789012", self.get("tasks/ABC-100.md"))
        self.assertEqual(self.writes(), 0)

    def test_hand_written_section_is_not_adopted(self) -> None:
        self.put("tasks/ABC-100.md", HAND + "\n## Plan\n- hand written\n")
        self.refused("Plan", "a", "some text here", "Plan")
        self.assertEqual(self.task(), HAND + "\n## Plan\n- hand written\n")

    def test_bad_ticket_key_and_jira_gate(self) -> None:
        code, out = self.one("Plan", "a", "some text here")
        self.assertEqual(code, 0, out)
        code, out = self.log("--section", "Plan", "--key", "a", "some text here", key="ZZZ-1")
        self.assertEqual(code, 2, out)

    def test_needs_either_form(self) -> None:
        self.assertEqual(self.log()[0], 2)
        self.assertEqual(self.log("--section", "Plan")[0], 2)


class Evidence(LogCase):
    def test_user_words_are_accepted(self) -> None:
        self.assertEqual(self.one("Plan", "a", "some text here")[0], 0)

    def test_the_ticket_being_written_is_accepted_when_touched(self) -> None:
        self.prompt("working on ABC-100 now please", "pid00002", tickets=["ABC-100"])
        code, out = self.one("Plan", "a", "some text here", evidence="ticket:ABC-100")
        self.assertEqual(code, 0, out)
        (rec,) = writelog.read(self.cfg)
        self.assertEqual(rec["evidence"], "ticket ABC-100")

    def test_other_ticket_and_repo_are_refused(self) -> None:
        self.prompt("working on both ABC-100 and ABC-7", "pid00002", tickets=["ABC-100", "ABC-7"])
        for evidence in ("ticket:ABC-7", "repo:alpha", "never typed anywhere in this session"):
            code, out = self.one("Plan", "a", "some text here", evidence=evidence)
            self.assertEqual(code, 2, out)
        code, out = self.one("Plan", "a", "some text here", evidence="ticket:ABC-7")
        self.assertIn("ticket:ABC-100", out)
        self.assertEqual(self.writes(), 0)

    def test_untouched_ticket_is_refused_and_evidence_is_required(self) -> None:
        self.assertEqual(self.one("Plan", "a", "some text here", evidence="ticket:ABC-100")[0], 2)
        self.assertEqual(self.one("Plan", "a", "some text here", evidence=None)[0], 2)


class Dispatch(LogCase):
    def test_show_still_works_and_log_does_not_fall_through(self) -> None:
        self.put("tasks/ABC-100.md", HAND)
        code, out = self.juno("task", "show", "ABC-100")
        self.assertEqual(code, 0, out)
        self.assertIn("## Notes", out)
        code, out = self.one("Plan", "a", "some text here")
        self.assertEqual(code, 0, out)
        self.assertNotIn("## Notes", out)
        self.assertIn("logged", out)


class NotConfigured(WriteCase):
    def test_same_message_as_task_set(self) -> None:
        code, out = self.juno(
            "task", "log", "ABC-100", "--section", "Plan", "--key", "a", "some text here",
            "--evidence", QUOTE,
        )  # fmt: skip
        self.assertEqual(code, 2)
        self.assertIn("jira not configured", out)
