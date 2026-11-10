import unittest

from juno.core import doctor_vault as dv
from juno.core import privacy

ABC = ("ABC",)
CLEAN = (
    "tool run INS-202611021105 and TASK-202611091236 passed",
    "stamp INS-20261102 and INV-20261019190355",
    "limit 102400 bytes and 4096 B, 10 KB, 5 MB, 2 GB",
    "took 250000 ms or 259200 s or 259200 seconds or 259200 sec",
    "wrote 102400bytes",
    "see comment 5632425 and Comment #5632425",
    "review 5626688704, Sentry issue 7790621826, Run 39486898219",
    "PR 1234567, pull #1234567, build 7654321, job 7654321",
)
FLAGGED = (
    "call 5551234567 now",
    "call +1 555 123 4567",
    "ref 123456789012",
    "customer 123456789",
    "user 123456789",
    "id 259200",
    "account 123456789012 in eu-west-1",
    "x=250000",
    "customer-123456789",
    "CUSTOMER-123456789",
    "USER-5551234567",
    "case TC-204095 failed",
    "INS-202613021105",
    "ABCDEFGHIJK-123456789",
    "mail jo@example.com",
)


class LongIdFalsePositives(unittest.TestCase):
    def test_clean_text_passes_with_and_without_prefixes(self) -> None:
        for text in CLEAN:
            for prefixes in (None, ABC):
                self.assertIsNone(privacy.scan(text, prefixes), text)
                self.assertEqual(privacy.kinds(text, prefixes), [], text)
                self.assertEqual(privacy.personal_spans(text, prefixes), [], text)
                self.assertEqual(privacy.mask(text, prefixes), (text, {}), text)

    def test_true_positives_stay_flagged(self) -> None:
        for text in FLAGGED:
            self.assertIsNotNone(privacy.scan(text, ABC), text)
            self.assertTrue(privacy.kinds(text, ABC), text)
            self.assertTrue(privacy.personal_spans(text, ABC), text)
            self.assertNotEqual(privacy.mask(text, ABC)[0], text, text)

    def test_unit_and_word_must_touch_the_number(self) -> None:
        for text in ("102400 and bytes", "comment on 5632425", "runs 39486898219", "prun 39486898219"):
            self.assertIsNotNone(privacy.scan(text), text)


class DoctorCardHeading(unittest.TestCase):
    def test_all_digit_segment_id_in_heading_is_not_flagged(self) -> None:
        head = "## 2026-10-21 13:11 · - · label · 25414656\nRepos: a\n"
        self.assertEqual(dv.privacy_checks({"h.md": head})[0][0], "PASS")

    def test_digits_elsewhere_are_still_flagged(self) -> None:
        body = "## 2026-10-21 13:11 · - · label · 25414656\ncall 5551234567\n"
        self.assertEqual(dv.privacy_checks({"h.md": body})[0][0], "WARN")
        label = "## 2026-10-21 13:11 · - · label 123456789 · 25414656\n"
        self.assertEqual(dv.privacy_checks({"h.md": label})[0][0], "WARN")
        note = "not a heading · 25414656\n"
        self.assertEqual(dv.privacy_checks({"h.md": note})[0][0], "WARN")


if __name__ == "__main__":
    unittest.main()
