import unittest

from juno.core import cardfmt, privacy, routing, writegate
from juno.core.errors import Refused
from juno.core.ledger import Segment
from tests.support.write import WriteCase

ABC = ("ABC",)
QUOTE = "this is the first typed prompt"
RULE = "Run the formatter before every commit"


class Scan(unittest.TestCase):
    def test_email_in_a_url_query_is_found(self) -> None:
        for url in (
            "https://app/u?email=jane.doe@corp.com",
            "https://app/u?email=jane.doe%40corp.com",
            "https://app/u#jane.doe@corp.com",
            "see https://app/u?phone=%2B15551234567 now",
            "https://app/u?uid=123456789",
        ):
            self.assertIsNotNone(privacy.scan(url, ABC), url)

    def test_normal_urls_and_ticket_keys_pass(self) -> None:
        for text in (
            "https://github.com/o/r/pull/1234567",
            "https://x.slack.com/archives/C0AAAAAAAAA/p1793112507638039",
            "https://jira.example.com/browse/ABC-123456?focusedId=42",
            "fixed ABC-123456 and abc-654321",
            "branch feature/ABC-444444-fix",
        ):
            self.assertIsNone(privacy.scan(text, ABC), text)

    def test_word_digits_tokens_are_ids_unless_a_configured_ticket(self) -> None:
        self.assertEqual(privacy.scan("customer-123456789", ABC), "id")
        self.assertEqual(privacy.scan("customer-123456789"), "id")
        self.assertEqual(privacy.scan("user_id=987654321 broke", ABC), "id")
        self.assertEqual(privacy.scan("CUSTOMER-123456789", ABC), "id")
        self.assertIsNone(privacy.scan("ABC-123456", ABC))
        self.assertIsNone(privacy.scan("ABC-123456"))

    def test_other_kinds(self) -> None:
        self.assertEqual(privacy.scan("mail jo@example.com", ABC), "email")
        self.assertEqual(privacy.scan("call +90 555 123 45 67", ABC), "phone")
        self.assertEqual(privacy.scan("token ghp_" + "a1B2c3D4e5F6" * 3, ABC), "secret")
        self.assertEqual(privacy.kinds("jo@example.com", ABC), ["email"])

    def test_clean_line_and_check_are_one_path(self) -> None:
        with self.assertRaises(Refused) as caught:
            privacy.clean_line("see https://app/u?email=a.b@c.io", "quote", ABC)
        self.assertIn("private data (email)", str(caught.exception))
        self.assertEqual(privacy.clean_line("  a   b ", "x", ABC), "a b")
        with self.assertRaises(Refused):
            privacy.clean_line("a · b", "x")


class Cards(unittest.TestCase):
    def test_validate_card_refuses_private_data(self) -> None:
        body = "Did it.\nLearned: ask jo@example.com\n"
        text = "## 2026-01-01 00:00 · ABC-1 · x · abcd1234\nRepos: r\nTickets: ABC-1\n" + body
        with self.assertRaises(cardfmt.Failed) as caught:
            cardfmt.validate_card(text, "abcd1234", "ABC-1", "2026-01-01 00:00", ABC)
        self.assertIn("email", str(caught.exception))

    def test_an_all_digit_self_source_id_is_not_private(self) -> None:
        line = '- Prefers asking first. · 2026-11-11 · src: user 26224046 · "soralım mı"'
        self.assertIsNone(privacy.scan(line))
        self.assertEqual(privacy.scan("call 26224046 today"), "id")

    def test_validate_card_allows_ticket_keys_and_digit_only_segment_ids(self) -> None:
        text = (
            "## 2026-01-01 00:00 · ABC-123456 · x · 12345678\nRepos: r\nTickets: ABC-123456\n"
            "Done ABC-123456.\nLearned: none\n"
        )
        out = cardfmt.validate_card(text, "12345678", "ABC-123456", "2026-01-01 00:00", ABC)
        self.assertIn("12345678", out)

    def test_validate_card_allows_an_all_digit_short_commit_hash(self) -> None:
        for line in (
            "Committed as 3590742 on the branch.",
            "Pushed commit 3590742.",
            "[feature/DEF-1-e2e 3590742] DEF-1",
            "Reverted 359074201234.",
        ):
            text = (
                "## 2026-01-01 00:00 · r · x · abcd1234\nRepos: r\nTickets: -\n"
                f"{line}\nLearned: none\n"
            )
            with self.subTest(line=line):
                out = cardfmt.validate_card(text, "abcd1234", "r", "2026-01-01 00:00", ABC)
                self.assertIn("3590742", out)

    def test_an_id_before_sentence_punctuation_is_still_private(self) -> None:
        for text in ("Customer 12345678.", "id 12345678. Next", "ids 1234567, 7654321."):
            with self.subTest(text=text):
                self.assertEqual(privacy.personal_kind(text), "id")
        for text in ("version 1234567.5", "file 1234567.txt"):
            with self.subTest(text=text):
                self.assertIsNone(privacy.personal_kind(text))

    def test_ids_near_commit_words_are_still_private(self) -> None:
        for line in (
            "Customer 3590742 reported it",
            "The partner id is 3590742",
            "Committed as 359074",
            "Hash of customer 12345678901234",
        ):
            with self.subTest(line=line):
                self.assertEqual(privacy.scan(line, ABC), "id")

    def test_stub_withholds_a_private_excerpt(self) -> None:
        seg = Segment("57fb7685-9be8-4bc7-a435-47aa50ccd35f")
        seg.last_msg = "write to jane.doe@corp.com about it"
        target = {"key": "-", "repos": [], "tickets": []}
        card = routing.stub_card(seg, target, 0.0, 300, ABC)
        self.assertIn("Last assistant message: -", card)
        seg.last_msg = "finished ABC-123456"
        self.assertIn("finished ABC-123456", routing.stub_card(seg, target, 0.0, 300, ABC))


class Mask(unittest.TestCase):
    def test_personal_spans_are_masked_and_rescan_clean(self) -> None:
        cases = {
            "Jira 2169327 moved": ("Jira <id> moved", {"id": 1}),
            "ids 2169327 and 7654321": ("ids <id> and <id>", {"id": 2}),
            "mail jane.doe@corp.com now": ("mail <email> now", {"email": 1}),
            "call +49 170 1234567 now": ("call <phone> now", {"phone": 1}),
            "see https://app/u?email=jane.doe%40corp.com ok": ("see <email> ok", {"email": 1}),
            "see https://app/u?uid=123456789 ok": ("see <id> ok", {"id": 1}),
        }
        for text, (want, counts) in cases.items():
            with self.subTest(text=text):
                self.assertIsNotNone(privacy.scan(text, ABC))
                out, got = privacy.mask(text, ABC)
                self.assertEqual((out, got), (want, counts))
                self.assertIsNone(privacy.scan(out, ABC))

    def test_protected_spans_are_left_alone(self) -> None:
        for text in (
            "https://github.com/o/r/pull/1234567",
            "https://x.slack.com/archives/C0AAAAAAAAA/p1793112507638039",
            "https://jira.example.com/browse/ABC-123456?focusedId=42",
            "fixed ABC-123456 and abc-654321",
            "branch feature/ABC-444444-fix",
            "Committed as 3590742",
            "cherry-picked 1234567 and [main (root-commit) 1234567]",
            "nothing here, 42 items",
        ):
            with self.subTest(text=text):
                self.assertIsNone(privacy.scan(text, ABC))
                self.assertEqual(privacy.mask(text, ABC), (text, {}))

    def test_protected_spans_survive_next_to_findings(self) -> None:
        text = "ABC-123456 touched 2169327; https://x.io/p/9 by a@b.co"
        self.assertEqual(
            privacy.mask(text, ABC)[0], "ABC-123456 touched <id>; https://x.io/p/9 by <email>"
        )

    def test_secrets_are_not_masked(self) -> None:
        text = "key ghp_" + "b" * 36
        self.assertEqual(privacy.mask(text, ABC), (text, {}))
        self.assertEqual(privacy.scan(text, ABC), "secret")

    def test_stub_names_the_card_failure(self) -> None:
        seg = Segment("57fb7685-9be8-4bc7-a435-47aa50ccd35f")
        target = {"key": "-", "repos": [], "tickets": []}
        plain = routing.stub_card(seg, target, 0.0, 300, ABC)
        self.assertIn("Segment ended without a card; Juno wrote this stub", plain)
        failed = routing.stub_card(seg, target, 0.0, 300, ABC, "invalid: private data (id)")
        self.assertIn("Card job failed (invalid: private data (id)); Juno wrote this stub", failed)
        self.assertNotIn("ended without a card", failed)

    def test_previous_segment_line_names_the_card_failure(self) -> None:
        seg = Segment("57fb7685-9be8-4bc7-a435-47aa50ccd35f")
        line = routing.previous_segment_line(seg, ["handoffs/r.md"], None, "invalid: heading")
        self.assertIn("card job failed (invalid: heading)", line)
        self.assertIn("not the previous segment's", line)
        self.assertNotIn("card job failed", routing.previous_segment_line(seg, ["x.md"]))


class WritePaths(WriteCase):
    TICKETS = True

    def refused(self, *argv: str) -> str:
        code, out = self.juno(*argv)
        self.assertEqual(code, 2, out)
        return out

    def test_rule_text_and_quote_are_scanned(self) -> None:
        self.prompt("mail jane.doe@corp.com always run the formatter", "pid00002")
        out = self.refused("rule", "add", "--scope", "global", "--quote", QUOTE, "Mail jo@x.io")
        self.assertIn("private data", out)
        out = self.refused(
            "rule",
            "add",
            "--scope",
            "global",
            "--quote",
            "mail jane.doe@corp.com always run the formatter",
            RULE,
        )
        self.assertIn("private data (email)", out)
        self.assertEqual(self.get("rules/global.md").count("\n- "), 0)

    def test_knowledge_url_is_scanned(self) -> None:
        out = self.refused(
            "knowledge",
            "add",
            "--topic",
            "api",
            "--source",
            "web",
            "--url",
            "https://app.example.com/u?email=jane.doe@corp.com",
            "The export endpoint pages by cursor",
        )
        self.assertIn("--url", out)
        code, out = self.juno(
            "knowledge",
            "add",
            "--topic",
            "api",
            "--source",
            "web",
            "--url",
            "https://docs.example.com/export/ABC-123456",
            "The export endpoint pages by cursor",
        )
        self.assertEqual(code, 0, out)

    def test_task_set_refuses_private_titles_and_logs_and_undoes(self) -> None:
        base = ("task", "set", "ABC-100", "--status", "open")
        self.refused(*base, "--title", "Fix mail to jo@example.com")
        self.refused(*base, "--title", "T", "--url", "https://x.test/a?e=jo@example.com")
        self.assertEqual(self.get("tasks/ABC-100.md"), "")
        code, out = self.juno(*base, "--title", "Fix the thing")
        self.assertEqual(code, 0, out)
        wid = out.split("(write ")[1].split(";")[0]
        self.assertIn("title: Fix the thing", self.get("tasks/ABC-100.md"))
        code, out = self.juno("undo", wid)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.get("tasks/ABC-100.md"), "")

    def test_task_set_is_capped_like_the_gate(self) -> None:
        for n in range(writegate.RUN_MAX):
            code, out = self.juno("task", "set", f"ABC-{n + 1}", "--title", "T", "--status", "open")
            self.assertEqual(code, 0, out)
        out = self.refused("task", "set", "ABC-99", "--title", "T", "--status", "open")
        self.assertIn(f"{writegate.RUN_MAX} vault writes", out)


if __name__ == "__main__":
    unittest.main()
