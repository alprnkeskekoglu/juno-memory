import unittest

from juno.core import cardfmt


class CardLinks(unittest.TestCase):
    def test_a_links_entry_needs_a_parenthesis_reason_right_after_the_link(self) -> None:
        text = "## h\nLinks: [[a]] and more words, [[b]] (the run commands), [[c|C]] ( ), [[d]]"
        fixed, unlinked = cardfmt.fix_links(text)
        self.assertEqual(fixed, "## h\nLinks: [[b]] (the run commands)")
        self.assertEqual(unlinked, 3)
        whole = "## h\nLinks: [[a]] (the run, then test), [[b]] (more here)\nok"
        self.assertEqual(cardfmt.fix_links(whole), (whole, 0))

    def test_a_card_with_only_reasonless_links_validates_with_links_dash(self) -> None:
        stamp = "2026-02-02 10:00"
        card = (
            f"## {stamp} · ABC-1 · work · abcd1234\nRepos: r\nTickets: -\nDid it.\n"
            "Links: [[a]], [[b]]\nLearned: none\n"
        )
        fixed, unlinked = cardfmt.fix_links(card)
        self.assertEqual(unlinked, 2)
        self.assertIn("\nLinks: -\n", fixed)
        self.assertIn("\nLinks: -\n", cardfmt.validate_card(fixed, "abcd1234", "ABC-1", stamp))
