import unittest

from juno.core import doctor_links
from tests.support.write import WriteCase

CARD = (
    "## 2026-11-01 10:00 · - · work · abcd1234\nRepos: app\nTickets: -\nDid a thing.\n"
    "{links}Learned: none\n"
)


class Pure(unittest.TestCase):
    def test_counts_card_links_without_a_reason(self) -> None:
        handoffs = {
            "handoffs/app.md": CARD.format(links="Links: [[a]], [[b]] (the run commands)\n")
            + CARD.format(links="Links: -\n")
            + CARD.format(links="Links: [[c]] \n"),
        }
        out = doctor_links.info_checks({}, handoffs)
        self.assertEqual(out, [("INFO", "links", "2 handoff card links have no reason")])

    def test_counts_notes_and_knowledge_with_no_link_in_or_out(self) -> None:
        files = {
            "notes/2026-11-01-lonely.md": "text only\n",
            "notes/2026-11-01-out.md": "text\n\nLinks: [[alpha]] (the profile here)\n",
            "notes/2026-11-01-in.md": "text\n",
            "knowledge/cache.md": "- fact\n",
            "knowledge/solo.md": "- fact\n",
            "projects/alpha.md": "see [[2026-11-01-in]] and [[cache]]\n",
            "tasks/ABC-1.md": "no links here\n",
        }
        out = doctor_links.info_checks(files, {})
        self.assertEqual(
            out, [("INFO", "links", "2 note or knowledge files have no link in or out")]
        )

    def test_a_card_link_counts_as_a_link_in(self) -> None:
        files = {"notes/2026-11-01-in.md": "text\n"}
        handoffs = {"handoffs/app.md": CARD.format(links="Links: [[2026-11-01-in]] (the note)\n")}
        self.assertEqual(doctor_links.info_checks(files, handoffs), [])

    def test_nothing_to_report_is_silent(self) -> None:
        self.assertEqual(doctor_links.info_checks({}, {}), [])


class Doctor(WriteCase):
    def test_doctor_prints_both_info_lines_and_no_warn_for_them(self) -> None:
        self.put("notes/2026-11-01-lonely.md", "---\ndate: 2026-11-01\n---\n\nA lonely note body\n")
        self.put("handoffs/app.md", "# H\n\n" + CARD.format(links="Links: [[core]]\n"))
        _, out = self.juno("doctor")
        lines = [x for x in out.splitlines() if "have no" in x]
        self.assertEqual(len(lines), 2, out)
        self.assertTrue(all("INFO" in x and "WARN" not in x for x in lines), lines)
