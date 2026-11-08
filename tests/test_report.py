import json
import os
import time
import unittest
from unittest import mock

from juno import config
from juno.adapters import reportauto
from juno.core import isoweek, report
from juno.core.errors import Refused
from tests.support.hooks import HookCase
from tests.support.reportseed import seed

LABEL = "2026-W45"
HEADERS = ("## Signals", "## Retrieval", "## Arms", "## Memory", "## Pending proposals")
NOW = time.mktime((2026, 11, 9, 9, 0, 0, 0, 0, -1))


class Weeks(unittest.TestCase):
    def test_last_week_of_a_monday_is_the_week_before(self) -> None:
        self.assertEqual(isoweek.last_week(NOW), "2026-W45")

    def test_bounds_span_seven_days(self) -> None:
        start, end = isoweek.bounds(LABEL)
        self.assertEqual(time.strftime("%Y-%m-%d", time.localtime(start)), "2026-11-02")
        self.assertEqual(time.strftime("%Y-%m-%d", time.localtime(end)), "2026-11-09")

    def test_bad_labels_are_refused(self) -> None:
        for label in ("2026-W54", "2026-45", "last"):
            with self.assertRaises(Refused):
                isoweek.bounds(label)

    def test_year_boundary(self) -> None:
        self.assertEqual(isoweek.previous("2026-W01"), "2025-W52")


class Pure(unittest.TestCase):
    def test_open_sections_skip_invariant_and_fenced(self) -> None:
        text = (
            "<!-- juno:invariant -->\n## Privacy\n<!-- /juno:invariant -->\n## Writing\n"
            "```\n## YYYY heading\n```\n## Cards\n"
        )
        self.assertEqual(report.open_sections(text), ["Writing", "Cards"])

    def test_duplicates_ignore_provenance_and_same_file(self) -> None:
        line = "- The export job times out on partners with many rows"
        files = {
            "knowledge/a.md": line,
            "knowledge/b.md": line + " today",
            "knowledge/c.md": "- From: [[handoffs/x]] " + line,
        }
        groups = report.duplicate_groups(files, "none")
        self.assertEqual(
            [sorted(g["files"]) for g in groups], [["knowledge/a.md", "knowledge/b.md"]]
        )

    def test_inbox_age(self) -> None:
        text = "# Inbox\n\n- x thing · aaaa · 2026-11-01\n"
        self.assertEqual(report.inbox_age(text, NOW), {"items": 1, "oldest_days": 8})


class Build(HookCase):
    def run_report(self, *extra: str) -> tuple[int, str]:
        return self.sb.cli("report", "--week", LABEL, *extra)

    def cfg(self) -> config.Config:
        return config.load(dict(self.env))

    def test_empty_state_prints_no_data_everywhere(self) -> None:
        code, out = self.run_report("--json")
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        self.assertEqual({k: v for k, v in data["signals"].items() if v is not None}, {})
        text = self.read(f"_generated/reports/{LABEL}.md")
        self.assertIn("corrections per 100 prompts: no data", text)
        self.assertIn("arms: no data", text)
        self.assertNotIn(": 0\n", text)

    def test_seeded_state_fills_every_section(self) -> None:
        self.sb.env["JUNO_CLAUDE_PROJECTS"] = self.env["JUNO_CLAUDE_PROJECTS"]
        seed(self.sb.state, self.sb.vault, LABEL, self.env["JUNO_CLAUDE_PROJECTS"])
        code, out = self.run_report()
        self.assertEqual(code, 0, out)
        text = self.read(f"_generated/reports/{LABEL}.md")
        self.assertIn("recall proxy (search only): 1/1 entries with evidence", text)
        self.assertIn("keys win 1, no-keys win 0, both 0, neither 0", text)
        for header in HEADERS:
            self.assertIn(header, text)
        self.assertNotIn("no data", text.split("## Retrieval")[0])
        self.assertIn("corrections per 100 prompts: 50.0 (2 of 4 prompts)", text)
        self.assertIn("Recommended-pick rate: 67% (2 of 3 answers)", text)
        self.assertIn("pushback per 100 proposals: 15.0 (3 of 20 proposals)", text)
        self.assertIn("card cost: $0.0400 per run (2 runs)", text)
        self.assertIn("pitfalls 1/2", text)
        self.assertIn("PreToolUse 2", text)
        self.assertIn("note 2 (1)", text)
        self.assertIn("Do not run the formatter on generated files (2 sessions)", text)
        self.assertIn("segs (running)", text)
        self.assertIn("stop rule: decide at min_n or max_days", text)
        self.assertIn("1 items, oldest", text)
        self.assertIn("cross-file duplicate groups: 1", text)
        self.assertIn("decay candidates", text)

    def test_the_run_is_repeatable_and_makes_no_model_call(self) -> None:
        seed(self.sb.state, self.sb.vault, LABEL)
        self.run_report()
        first = self.read(f"_generated/reports/{LABEL}.md")
        self.run_report()
        second = self.read(f"_generated/reports/{LABEL}.md")
        self.assertEqual(first.split("\n", 3)[3], second.split("\n", 3)[3])
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "fakeclaude.log")))

    def test_level_three_names_only_open_core_sections(self) -> None:
        seed(self.sb.state, self.sb.vault, LABEL)
        before, _ = isoweek.bounds(isoweek.previous(LABEL))
        now, _ = isoweek.bounds(LABEL)
        rows = [
            {"ts": int(before + 3600 + i), "segment": "old", "prompt": text, "suggestions": []}
            for i, text in enumerate(["always x"] * 3 + ["fine"] * 27)
        ]
        rows += [
            {"ts": int(now + 3600 + i), "segment": "new", "prompt": "never y", "suggestions": []}
            for i in range(20)
        ]
        path = os.path.join(self.sb.state, "shadow.jsonl")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("".join(json.dumps(r) + "\n" for r in rows))
        code, out = self.run_report("--json")
        self.assertEqual(code, 0, out)
        levels = [p for p in json.loads(out)["proposals"] if p["level"] == 3]
        self.assertEqual(len(levels), 1, levels)
        self.assertIn("corrections up", levels[0]["text"])
        self.assertIn("Writing the vault", levels[0]["text"])
        for invariant in ("Privacy", "Trust", "Rules"):
            self.assertNotIn(invariant, levels[0]["text"])

    def test_bad_week_is_refused(self) -> None:
        code, _ = self.sb.cli("report", "--week", "2026-W99")
        self.assertEqual(code, 2)


class Auto(HookCase):
    def spawned(self, now: float) -> list[object]:
        with mock.patch("subprocess.Popen") as popen:
            reportauto.tick(self.cfg(), now)
        return list(popen.call_args_list)

    def cfg(self) -> config.Config:
        return config.load(dict(self.env))

    def test_no_history_spawns_nothing(self) -> None:
        self.assertEqual(self.spawned(NOW), [])

    def test_missing_report_spawns_once_per_window(self) -> None:
        seed(self.sb.state, self.sb.vault, LABEL)
        calls = self.spawned(NOW)
        self.assertEqual(len(calls), 1)
        argv = calls[0].args[0]  # type: ignore[attr-defined]
        self.assertEqual(argv[-3:], ["report", "--week", LABEL])
        self.assertEqual(self.spawned(NOW + 60), [])
        self.assertEqual(len(self.spawned(NOW + 7 * 3600)), 1)

    def test_ready_report_adds_the_line_once(self) -> None:
        self.write(f"_generated/reports/{LABEL}.md", "# Weekly report\n")
        first = reportauto.tick(self.cfg(), NOW)
        self.assertEqual(first, f"Juno: weekly report {LABEL} ready: _generated/reports/{LABEL}.md")
        self.assertEqual(reportauto.tick(self.cfg(), NOW + 60), "")

    def test_session_start_skips_after_compact(self) -> None:
        self.write(f"_generated/reports/{isoweek.last_week(time.time())}.md", "# r\n")
        fresh = self.context(self.hook("session_start"))
        self.assertIn("weekly report", fresh)
        label = isoweek.last_week(time.time())
        os.unlink(os.path.join(self.sb.state, reportauto.NOTIFIED_FILE))
        compact = self.payload("SessionStart", source="compact")
        self.assertNotIn(label, self.context(self.hook("session_start", compact)))


if __name__ == "__main__":
    unittest.main()
