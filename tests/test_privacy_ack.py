import json
import unittest

from juno.adapters import writelog
from juno.core import privacyack
from tests.support.cardcase import CardCase, card
from tests.test_writegate_cli import QUOTE, SECRET, Base

# The four shapes of issue #22, with made-up values.
SHAPES = (
    ("000000000000", "The mirror rewrites images to the 000000000000 pull-through cache"),
    ("0000000000", "The runner image tag is pinned (AB-0000000000) for the nightly job"),
    ("0000000000", "The release journey 0000000000 is documented for the team"),
    ("0000000", "Seen once: repro on demo 0000000 under the old runner"),
)
REF = "notes/sample.md:3"


class AckBase(Base):
    def put_line(self, line: str, rel: str = "notes/sample.md") -> None:
        self.put(rel, f"# Sample\n\n- {line}\n")

    def privacy_lines(self) -> list[str]:
        return [x for x in self.juno("doctor")[1].splitlines() if "privacy" in x]

    def warns(self) -> bool:
        return any("WARN" in x for x in self.privacy_lines())

    def ack(self, was: str, ref: str = REF) -> str:
        return self.ok("privacy", "ack", ref, "--was", was, "--evidence", QUOTE)

    def refuse_ack(self, was: str, ref: str = REF, evidence: str = QUOTE) -> str:
        return self.refused("privacy", "ack", ref, "--was", was, "--evidence", evidence)


class Ack(AckBase):
    def test_four_shapes_warn_then_pass_after_ack(self) -> None:
        for digits, line in SHAPES:
            with self.subTest(digits=digits):
                self.put_line(line)
                self.assertTrue(self.warns(), line)
                wid = self.wid(self.ack(digits))
                joined = "\n".join(self.privacy_lines())
                self.assertFalse(self.warns(), joined)
                self.assertIn("1 acknowledged id finding", joined)
                self.ok("undo", wid)

    def test_changed_digits_need_a_new_ack(self) -> None:
        self.put_line(SHAPES[0][1])
        self.ack("000000000000")
        self.put_line(SHAPES[0][1].replace("000000000000", "000000000001"))
        self.assertTrue(self.warns())
        self.refuse_ack("000000000000")
        self.ack("000000000001")
        self.assertFalse(self.warns())

    def test_another_id_in_the_file_still_warns(self) -> None:
        self.put_line(SHAPES[0][1] + " and also 111111111111")
        self.ack("000000000000")
        self.assertTrue(self.warns())

    def test_only_id_findings_can_be_acknowledged(self) -> None:
        cases = (
            (f"The token is {SECRET} for the job", "a1B2c3D4"),
            ("Call 555 123 45 67 about the job", "555 123 45 67"),
            ("Ask jo@example.com about the job", "jo@example.com"),
            ("Phone +1 555 123 4567 and 000000000000 here", "000000000000"),
        )
        for line, was in cases:
            with self.subTest(line=line):
                self.put_line(line)
                self.assertIn("id", self.refuse_ack(was))
        self.assertEqual(self.get(".privacy-ack.jsonl"), "")

    def test_was_ref_and_evidence_are_checked(self) -> None:
        self.put_line(SHAPES[0][1])
        self.refuse_ack("111111111111")
        self.refuse_ack("000000000000", ref="notes/sample.md:99")
        self.refuse_ack("000000000000", ref="notes/missing.md:1")
        self.refuse_ack("000000000000", evidence="words nobody typed here")
        self.assertEqual(self.get(".privacy-ack.jsonl"), "")

    def test_record_holds_a_hash_not_the_digits(self) -> None:
        self.put_line(SHAPES[0][1])
        self.ack("000000000000")
        raw = self.get(".privacy-ack.jsonl")
        self.assertNotIn("000000000000", raw)
        rec = json.loads(raw)
        self.assertEqual(
            (rec["file"], rec["line"], rec["evidence"]), ("notes/sample.md", 3, "user pid00001")
        )
        self.assertIn("already recorded", self.ack("000000000000"))

    def test_undo_removes_the_ack(self) -> None:
        self.put_line(SHAPES[0][1])
        wid = self.wid(self.ack("000000000000"))
        self.assertIn(wid, [r["id"] for r in writelog.read(self.cfg)])
        self.assertFalse(self.warns())
        self.ok("undo", wid)
        self.assertTrue(self.warns())
        self.assertEqual(self.get(".privacy-ack.jsonl"), "")

    def test_list_shows_file_line_when_and_evidence(self) -> None:
        self.assertIn("no acknowledged", self.ok("privacy", "list"))
        self.put_line(SHAPES[0][1])
        self.ack("000000000000")
        out = self.ok("privacy", "list")
        self.assertIn("notes/sample.md:3", out)
        self.assertIn("user pid00001", out)
        self.assertNotIn("000000000000", out)
        self.put_line("a different line without digits")
        self.assertIn("line gone", self.ok("privacy", "list"))

    def test_ignore_file_is_not_memory(self) -> None:
        self.put_line(SHAPES[0][1])
        self.ack("000000000000")
        self.assertNotIn(".privacy-ack", self.ok("search", "acknowledged"))
        self.assertFalse(self.warns())


class CardMask(CardCase):
    def ack_in_vault(self, digits: str) -> None:
        rec = privacyack.record("notes/sample.md", digits, 3, "user p1", "2026-01-01")
        with open(self.vp(".privacy-ack.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(privacyack.add("", rec))

    def test_card_keeps_an_acknowledged_span(self) -> None:
        self.dirty()
        self.ack_in_vault("000000000000")
        row = self.run_card(card(body="Pulled images from 000000000000 and call 2169327."))
        self.assertEqual(row["masked"], {"id": 1})
        written = self.handoff()
        self.assertIn("000000000000", written)
        self.assertNotIn("2169327", written)

    def test_card_with_only_an_acknowledged_span_is_not_masked(self) -> None:
        self.dirty()
        self.ack_in_vault("000000000000")
        row = self.run_card(card(body="Pulled images from 000000000000 today."))
        self.assertNotIn("masked", row)
        self.assertIn("000000000000", self.handoff())

    def test_card_still_masks_an_unacknowledged_span(self) -> None:
        self.dirty()
        row = self.run_card(card(body="Pulled images from 000000000000 today."))
        self.assertEqual(row["masked"], {"id": 1})
        self.assertNotIn("000000000000", self.handoff())


if __name__ == "__main__":
    unittest.main()
