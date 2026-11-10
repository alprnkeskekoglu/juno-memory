import json
import unittest

from juno.core import doctor_vault as dv
from juno.core import privacy
from tests.support.write import WriteCase

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


ALLOW = ("ABC", "TC", "XY")
ALLOWED = ("TC-123456 steps 1, 4", "KEY (XY-1234567890)")
OBJECTS = (
    "a comment (1234567) and",
    "run: 12345678",
    "issue=1234567",
    "build : 7654321",
    "PR(1234567)",
    "role example-service-20260101120000000000000001",
)
STILL = (
    "CUSTOMER-123456789",
    "USER-5551234567",
    "customer-123456789",
    "role-5551234567",
    "call 5551234567",
    "call 55512345678",
    "ref 123456789012",
    "customer 123456789",
    "ZZ-123456",
    "TCX-123456",
)


class AllowPrefixesAndObjectIds(unittest.TestCase):
    def test_allow_prefixes_exempt_only_listed_keys(self) -> None:
        for text in ALLOWED:
            self.assertIsNone(privacy.scan(text, ALLOW), text)
            self.assertEqual(privacy.personal_spans(text, ALLOW), [], text)
            self.assertEqual(privacy.scan(text, ABC), "id", text)

    def test_object_ids_and_long_digit_runs_are_exempt(self) -> None:
        for text in OBJECTS:
            for prefixes in (None, ABC, ALLOW):
                self.assertIsNone(privacy.scan(text, prefixes), text)
                self.assertEqual(privacy.mask(text, prefixes), (text, {}), text)

    def test_personal_ids_stay_flagged(self) -> None:
        for text in STILL:
            self.assertIsNotNone(privacy.scan(text, ALLOW), text)
            self.assertTrue(privacy.personal_spans(text, ALLOW), text)

    def test_twenty_digits_are_still_ids_but_hyphen_names_are_not_blanket_exempt(self) -> None:
        self.assertEqual(privacy.scan("x-" + "1" * 20), "id")
        self.assertEqual(privacy.scan("a " + "1" * 21), None)
        self.assertEqual(privacy.scan("customer-" + "1" * 21), None)
        self.assertEqual(privacy.scan("customer-" + "1" * 9), "id")


class DoctorAllowPrefixes(WriteCase):
    def privacy_status(self) -> str:
        code, out = self.juno("doctor", "--json", *self.sb.install_args())
        found = [c["status"] for c in json.loads(out)["checks"] if c["name"] == "privacy"]
        return found[0]

    def test_config_allow_prefixes_reaches_the_doctor_scan(self) -> None:
        self.set_config('[jira]\nprefixes = ["ABC"]\n')
        self.put("notes/n.md", "case TC-123456 steps 1, 4\n")
        self.assertEqual(self.privacy_status(), "WARN")
        self.set_config('[privacy]\nallow_prefixes = ["TC"]\n')
        self.assertEqual(self.privacy_status(), "PASS")
        self.put("notes/m.md", "case ZZ-123456\n")
        self.assertEqual(self.privacy_status(), "WARN")


if __name__ == "__main__":
    unittest.main()
