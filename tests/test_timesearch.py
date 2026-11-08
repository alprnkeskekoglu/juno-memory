import datetime
import os
import sqlite3
import unittest

from juno.adapters import index
from juno.core import search as core
from juno.core.vaultfmt import parse_frontmatter
from tests.support import SandboxCase
from tests.test_index import VaultCase

MTIME_NS = int(datetime.datetime(2026, 3, 4, 12, 0).timestamp() * 1e9)


def fields(text: str) -> dict[str, object]:
    return parse_frontmatter(text)[0]


class DayExtraction(unittest.TestCase):
    def test_newest_valid_date_wins_and_bad_dates_are_skipped(self) -> None:
        self.assertEqual(core.newest_day("a 2026-10-01 b 2026-10-25 c"), "2026-10-25")
        self.assertEqual(core.newest_day("2026-13-40 and 2026-02-30"), "")
        self.assertEqual(core.newest_day("id 12026-10-25 x 2026-10-251"), "")

    def test_passage_day_reads_heading_then_body_else_falls_back(self) -> None:
        self.assertEqual(core.passage_day("2026-10-18 10:02 · k", "x", "2026-01-01"), "2026-10-18")
        self.assertEqual(core.passage_day("h", "was 2026-09-01 then 2026-09-05", "z"), "2026-09-05")
        self.assertEqual(core.passage_day("h", "no date", "2026-01-01"), "2026-01-01")

    def test_file_day_order(self) -> None:
        both = fields("---\nupdated: 2026-05-02\nvalid_from: 2026-04-01\n---\n")
        self.assertEqual(core.file_day("notes/2026-01-01-a.md", both, MTIME_NS), "2026-05-02")
        valid = fields("---\nvalid_from: 2026-04-01\ncreated: 2026-02-01\n---\n")
        self.assertEqual(core.file_day("notes/a.md", valid, MTIME_NS), "2026-04-01")
        created = fields("---\ncreated: 2026-02-01\n---\n")
        self.assertEqual(core.file_day("notes/a.md", created, MTIME_NS), "2026-02-01")
        self.assertEqual(core.file_day("notes/2026-01-09-a.md", {}, MTIME_NS), "2026-01-09")
        self.assertEqual(core.file_day("knowledge/2026-01-09-a.md", {}, MTIME_NS), "2026-03-04")

    def test_history_flag_only_under_decisions(self) -> None:
        self.assertTrue(core.is_superseded("decisions/t.md", "History"))
        self.assertTrue(core.is_superseded("decisions/t.md", "Topic > History"))
        self.assertFalse(core.is_superseded("decisions/t.md", "Current"))
        self.assertFalse(core.is_superseded("notes/t.md", "History"))


class IndexedDays(VaultCase):
    def test_since_until_are_inclusive_and_a_history_passage_is_flagged(self) -> None:
        self.write("handoffs/a.md", "# H\n\n## 2026-10-01 09:00 · - · c · aaaaaaa1\ntimeterm old\n")
        self.write("handoffs/b.md", "# H\n\n## 2026-10-20 09:00 · - · c · aaaaaaa2\ntimeterm new\n")
        self.write(
            "decisions/t.md",
            "---\nupdated: 2026-10-25\n---\n# T\n\n## Current\n\n- timeterm live (user x, 2026-10-25)\n"
            "\n## History\n\n- timeterm dropped · superseded 2026-10-26\n",
        )
        self.build()
        found = self.search("timeterm", limit=10)
        by_day = {(r["relpath"], r["day"]) for r in found}
        self.assertIn(("handoffs/a.md", "2026-10-01"), by_day)
        self.assertEqual(self.paths("timeterm", until="2026-10-01"), ["handoffs/a.md"])
        self.assertEqual(
            self.paths("timeterm", since="2026-10-01", until="2026-10-01"), ["handoffs/a.md"]
        )
        self.assertNotIn("handoffs/a.md", self.paths("timeterm", since="2026-10-02", limit=10))
        self.assertEqual(self.paths("timeterm", since="2026-11-01"), [])
        flagged = [r for r in self.search("timeterm", limit=10) if r.get("superseded")]
        self.assertEqual([r["heading"] for r in flagged], ["T > History"])

    def test_no_filter_keeps_every_passage_and_schema_is_3(self) -> None:
        self.write("notes/a.md", "# A\n\nplainterm\n")
        self.build()
        self.assertEqual(index.SCHEMA_VERSION, 3)
        self.assertEqual(self.paths("plainterm"), ["notes/a.md"])

    def test_old_schema_is_rebuilt_with_days(self) -> None:
        self.write("notes/a.md", "# A\n\nplainterm 2026-10-05\n")
        self.build()
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA user_version=2")
        conn.close()
        self.assertEqual(self.build()["reindexed"], 1)
        self.assertEqual(self.search("plainterm")[0]["day"], "2026-10-05")

    def test_ticket_file_respects_the_filter(self) -> None:
        self.write(
            "handoffs/AB-1.md", "# H\n\n## 2026-10-01 09:00 · AB-1 · c · aaaaaaa1\nsome text\n"
        )
        self.build()
        self.assertEqual(self.paths("AB-1", since="2026-10-01"), ["handoffs/AB-1.md"])
        self.assertEqual(self.paths("AB-1", since="2026-10-02"), [])


class SearchCliTime(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)

    def write(self, rel: str, text: str) -> None:
        path = os.path.join(self.sb.vault, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_since_until_flags_filter_and_render_the_day(self) -> None:
        self.write(
            "handoffs/h1.md", "# H\n\n## 2026-10-01 09:00 · - · c · aaaaaaa1\ntimeterm old\n"
        )
        self.write(
            "handoffs/h2.md", "# H\n\n## 2026-10-20 09:00 · - · c · aaaaaaa2\ntimeterm new\n"
        )
        code, out = self.sb.cli(
            "search", "timeterm", "--since", "2026-10-10", "--until", "2026-10-31"
        )
        self.assertEqual(code, 0)
        self.assertIn("handoffs/h2.md · H > 2026-10-20 09:00 · - · c · aaaaaaa2 · 2026-10-20", out)
        self.assertNotIn("h1.md", out)

    def test_bad_or_reversed_dates_are_refused(self) -> None:
        for argv in (("--since", "yesterday"), ("--until", "2026-13-01"), ("--since", "2026-1-1")):
            code, _ = self.sb.cli("search", "x", *argv)
            self.assertEqual(code, 2, argv)
        code, _ = self.sb.cli("search", "x", "--since", "2026-10-02", "--until", "2026-10-01")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
