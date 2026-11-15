import dataclasses
import unittest

from juno.adapters.vaultview import DiskVault
from juno.core import packs, taskdigest, trim
from juno.core.packs import Caps
from tests.support.write import WriteCase
from tests.test_packs import FakeVault
from tests.test_task_digest_pack import NOTES, ticket

QUOTE = "this is the first typed prompt"
PAD = "w" * 100


def decisions(count: int) -> str:
    lines = "".join(
        f"- decision {n:02d} {'x' * 70} (user pid00001, 2026-10-01)\n" for n in range(count)
    )
    return "## Decisions\n" + lines + "\n"


def section(name: str, count: int, day: str, pad: str = PAD) -> str:
    lines = "".join(f"- [{name.lower()}{n:02d}] {pad} · {day}\n" for n in range(count))
    return f"## {name}\n{lines}\n"


def render(doc: str, chars: int = 4500) -> str:
    view = FakeVault({"tasks/ABC-100.md": doc})
    caps = dataclasses.replace(Caps(), pack_chars=chars)
    return packs.render_pack(view, caps, "ticket", "ABC-100", [])


def kept(text: str, name: str) -> int:
    return text.count(f"- [{name.lower()}")


REPORTER = ticket(
    "digests: [Plan, Work, QA]\n",
    decisions(32),
    section("Plan", 4, "2026-11-01"),
    section("Work", 10, "2026-11-02"),
    section("QA", 14, "2026-11-03"),
    NOTES,
)


class Priority(unittest.TestCase):
    def prio(self, *days: str) -> list[int]:
        names = ["Plan", "Work", "QA"][: len(days)]
        blocks = [(f"{n} · t.md", section(n, 2, d)) for n, d in zip(names, days, strict=True)]
        return taskdigest.priority(blocks)

    def test_the_section_with_the_latest_line_date_comes_first(self) -> None:
        self.assertEqual(self.prio("2026-11-01", "2026-11-02", "2026-11-03"), [2, 1, 0])
        self.assertEqual(self.prio("2026-11-03", "2026-11-01", "2026-11-02"), [0, 2, 1])

    def test_a_tie_goes_to_the_section_registered_later(self) -> None:
        self.assertEqual(self.prio("2026-11-02", "2026-11-02", "2026-11-02"), [2, 1, 0])

    def test_the_date_is_the_max_over_the_lines(self) -> None:
        body = "- [a] x · 2026-11-09\n- [b] y · 2026-11-01"
        other = "- [c] z · 2026-11-05"
        self.assertEqual(taskdigest.priority([("A · t", body), ("B · t", other)]), [0, 1])

    def test_a_section_without_dates_is_the_oldest(self) -> None:
        blocks = [("A · t", "- [a] no date"), ("B · t", "- [b] x · 2026-11-01")]
        self.assertEqual(taskdigest.priority(blocks), [1, 0])


class ReporterShape(unittest.TestCase):
    def test_the_newest_section_is_kept_before_the_oldest(self) -> None:
        text = render(REPORTER)
        self.assertLessEqual(len(text), 4500)
        self.assertGreaterEqual(kept(text, "QA"), 10)
        self.assertIn("- [qa13]", text)
        self.assertGreater(kept(text, "Work"), 0)
        self.assertLess(kept(text, "Plan"), 4)

    def test_older_sections_are_cut_first_when_the_newest_fills_the_room(self) -> None:
        doc = ticket(
            "digests: [Plan, Work, QA]\n",
            decisions(32),
            section("Plan", 14, "2026-11-01"),
            section("Work", 14, "2026-11-02"),
            section("QA", 14, "2026-11-03"),
            NOTES,
        )
        text = render(doc)
        self.assertGreaterEqual(kept(text, "QA"), kept(text, "Work"))
        self.assertGreaterEqual(kept(text, "Work"), kept(text, "Plan"))
        self.assertGreater(kept(text, "QA"), kept(text, "Plan"))
        self.assertIn("- [qa13]", text)

    def test_a_date_tie_is_broken_by_the_digests_order(self) -> None:
        doc = ticket(
            "digests: [Plan, Work, QA]\n",
            decisions(32),
            section("Plan", 14, "2026-11-03"),
            section("Work", 14, "2026-11-03"),
            section("QA", 14, "2026-11-03"),
            NOTES,
        )
        text = render(doc)
        self.assertGreater(kept(text, "QA"), kept(text, "Plan"))

    def test_the_newest_written_section_wins_over_a_later_registered_one(self) -> None:
        doc = ticket(
            "digests: [Plan, Work, QA]\n",
            decisions(32),
            section("Plan", 14, "2026-11-09"),
            section("Work", 14, "2026-11-02"),
            section("QA", 14, "2026-11-03"),
            NOTES,
        )
        text = render(doc)
        self.assertGreater(kept(text, "Plan"), kept(text, "QA"))
        self.assertGreater(kept(text, "QA"), kept(text, "Work"))

    def test_decisions_that_fit_the_share_all_survive(self) -> None:
        text = render(ticket(
            "digests: [Plan, Work, QA]\n",
            decisions(15),
            section("Plan", 14, "2026-11-01"),
            section("Work", 14, "2026-11-02"),
            section("QA", 14, "2026-11-03"),
            NOTES,
        ))  # fmt: skip
        self.assertEqual(text.count("- decision"), 15)
        self.assertGreater(kept(text, "QA"), 0)

    def test_display_order_stays_the_digests_order(self) -> None:
        text = render(REPORTER)
        order = ["[Decisions ·", "[Plan ·", "[Work ·", "[QA ·", "[Notes ·"]
        positions = [text.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Juno truncated", text)

    def test_a_tight_budget_keeps_the_newest_line_of_the_newest_section(self) -> None:
        short = "short words"
        doc = ticket(
            "digests: [Plan, Work, QA]\n",
            decisions(32),
            section("Plan", 6, "2026-11-01", short),
            section("Work", 6, "2026-11-02", short),
            section("QA", 6, "2026-11-03", short),
            NOTES,
        )
        text = render(doc, 600)
        self.assertLessEqual(len(text), 600)
        self.assertIn("- [qa05]", text)


class TrimPackPriority(unittest.TestCase):
    def test_priority_none_keeps_the_old_sharing(self) -> None:
        sections = [("D · d.md", "- d\n" * 100), ("P · t.md", "- p\n" * 100)]
        full = "\n".join(f"[{t}]\n{b}" for t, b in sections)
        self.assertEqual(
            trim.trim_pack(["h"], sections, 400, full, reserve_first=True),
            trim.trim_pack(["h"], sections, 400, full, reserve_first=True, priority=None),
        )


class RealStack(WriteCase):
    TICKETS = True

    def log(self, section_name: str, count: int) -> None:
        for n in range(count):
            code, out = self.juno(
                "task", "log", "ABC-80", "--section", section_name, f"--key=k{n:02d}",
                "--evidence", QUOTE, f"{section_name} step {n} " + "with padding words " * 6,
            )  # fmt: skip
            self.assertEqual(code, 0, out)

    def test_logged_sections_keep_the_newest_written_one_under_a_short_budget(self) -> None:
        self.put("tasks/ABC-80.md", "---\njira: ABC-80\ntitle: T\n---\n\n## Notes\n- a note\n")
        for n in range(6):
            code, out = self.juno(
                "decision", "--topic", "ABC-80", "--evidence", QUOTE, f"Decision {n} " + "z" * 90
            )
            self.assertEqual(code, 0, out)
        self.log("Plan", 10)
        self.log("Work", 10)
        self.log("QA", 10)
        # all lines carry today's date: the tie goes to the section registered last
        caps = dataclasses.replace(packs.Caps.from_config(self.cfg), pack_chars=2200)
        out = packs.render_pack(DiskVault(self.cfg), caps, "ticket", "ABC-80", [])
        self.assertLessEqual(len(out), 2200)
        self.assertGreater(out.count("QA step"), out.count("Plan step"))
        self.assertGreater(out.count("QA step"), 0)
        self.assertIn("Decision 5 ", out)


if __name__ == "__main__":
    unittest.main()
