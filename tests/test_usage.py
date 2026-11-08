import json
import os
import time
import unittest

from juno import config
from juno.adapters import armseg, cardwrite, prune, usageio
from juno.core import decay
from juno.core.ledger import Segment
from tests.support.hooks import SID, HookCase

DAY = 86400
FACT = "---\nupdated: 2026-01-01\ncreated: {day}\n{extra}---\n# Fact\n\nBody line.\n"


class Trace(HookCase):
    def cfg(self) -> config.Config:
        return config.load(dict(self.env))

    def rows(self) -> list[dict[str, object]]:
        return usageio.read_rows(self.sb.state)

    def touch(self, tool: str, key: str, value: str, **fields: object) -> None:
        self.hook(
            "post_tool_use",
            self.payload("PostToolUse", tool_name=tool, tool_input={key: value}, **fields),
        )

    def test_read_grep_and_glob_under_the_vault_are_logged(self) -> None:
        self.write("knowledge/a.md", FACT.format(day="2026-01-01", extra=""))
        self.touch("Read", "file_path", self.vp("knowledge/a.md"))
        self.touch("Grep", "path", self.vp("knowledge/a.md"))
        self.touch("Glob", "path", self.vp("knowledge"))
        rows = self.rows()
        self.assertEqual(
            [(r["rel"], r["ev"], r["sid8"]) for r in rows],
            [
                ("knowledge/a.md", "read", SID[:8]),
                ("knowledge/a.md", "read", SID[:8]),
                ("knowledge", "read", SID[:8]),
            ],
        )

    def test_subagent_read_is_logged(self) -> None:
        self.touch("Read", "file_path", self.vp("knowledge/a.md"), agent_id="a1")
        self.assertEqual([r["rel"] for r in self.rows()], ["knowledge/a.md"])

    def test_paths_outside_the_vault_and_other_tools_are_ignored(self) -> None:
        self.touch("Read", "file_path", os.path.join(self.work, "x.md"))
        self.touch("Edit", "file_path", self.vp("knowledge/a.md"))
        self.touch("Bash", "command", "cat " + self.vp("knowledge/a.md"))
        self.assertEqual(self.rows(), [])
        self.assertFalse(os.path.exists(usageio.path(self.sb.state)))

    def test_read_touches_are_per_segment_and_none_without_rows(self) -> None:
        self.touch("Read", "file_path", self.vp("knowledge/a.md"))
        touches = usageio.read_touches(self.sb.state, SID)
        self.assertEqual([rel for _, rel in touches or []], ["knowledge/a.md"])
        self.assertIsNone(usageio.read_touches(self.sb.state, "ffffffff-0000"))

    def test_the_linked_metric_prefers_trace_reads_over_transcripts(self) -> None:
        self.touch("Read", "file_path", self.vp("knowledge/a.md"))
        got = armseg.reads_of(self.cfg(), SID)
        self.assertEqual([rel for _, rel in got or []], ["knowledge/a.md"])
        self.assertIsNone(armseg.reads_of(self.cfg(), "ffffffff-0000"))

    def test_card_links_are_logged_as_card_signals(self) -> None:
        self.write("knowledge/a.md", FACT.format(day="2026-01-01", extra=""))
        cardwrite.log_card_links(self.cfg(), Segment(SID), "Used [[knowledge/a]] and [[nope]].")
        self.assertEqual([(r["rel"], r["ev"]) for r in self.rows()], [("knowledge/a.md", "card")])

    def test_cited_vault_files_are_found_in_evidence(self) -> None:
        self.write("knowledge/a.md", FACT.format(day="2026-01-01", extra=""))
        found = usageio.cited_rels(
            self.cfg(),
            [
                "read knowledge/a.md and /tmp/other.md",
                "see " + self.vp("knowledge/a.md"),
                "knowledge/missing.md",
            ],
        )
        self.assertEqual(found, ["knowledge/a.md", "knowledge/a.md"])

    def test_prune_drops_rows_older_than_usage_days(self) -> None:
        now = time.time()
        os.makedirs(self.sb.state, exist_ok=True)
        old = {"t": now - 400 * DAY, "sid8": "x", "rel": "a.md", "ev": "read"}
        new = {"t": now - DAY, "sid8": "x", "rel": "b.md", "ev": "read"}
        with open(usageio.path(self.sb.state), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(old) + "\n" + json.dumps(new) + "\nnot json\n")
        prune.prune_state(self.cfg(), now)
        self.assertEqual([r["rel"] for r in self.rows()], ["b.md"])


class Report(unittest.TestCase):
    NOW = 1_000 * DAY

    def row(self, rel: str, days_ago: float) -> dict[str, object]:
        return {"t": self.NOW - days_ago * DAY, "sid8": "x", "rel": rel, "ev": "read"}

    def test_window_counts_from_the_trace_start(self) -> None:
        files = {"knowledge/a.md": self.NOW - 500 * DAY}
        rows = [self.row("other.md", 30)]
        out = decay.report(files, rows, self.NOW, 90)
        self.assertEqual(out["candidates"], [])
        rows = [self.row("other.md", 91)]
        out = decay.report(files, rows, self.NOW, 90)
        self.assertEqual([c["path"] for c in out["candidates"]], ["knowledge/a.md"])

    def test_a_recent_signal_or_a_young_file_is_kept(self) -> None:
        files = {"knowledge/a.md": self.NOW - 500 * DAY, "knowledge/b.md": self.NOW - 10 * DAY}
        rows = [self.row("knowledge/a.md", 5), self.row("x.md", 200)]
        out = decay.report(files, rows, self.NOW, 90)
        self.assertEqual(out["candidates"], [])

    def test_no_trace_lists_nothing(self) -> None:
        out = decay.report({"knowledge/a.md": 0.0}, [], self.NOW, 90)
        self.assertEqual((out["candidates"], out["trace_start"]), ([], None))
        self.assertIn("No usage trace", decay.render(out))


class Scan(HookCase):
    def test_private_archived_and_retired_files_are_excluded(self) -> None:
        from juno.cmd import decay as cmd

        day = "2026-01-01"
        self.write("knowledge/live.md", FACT.format(day=day, extra=""))
        self.write("knowledge/secret.md", FACT.format(day=day, extra="visibility: private\n"))
        self.write("knowledge/old.md", FACT.format(day=day, extra="status: archived\n"))
        self.write("handoffs/card.md", "# card\n")
        found = cmd.scan(config.load(dict(self.env)))
        self.assertEqual(sorted(found), ["knowledge/live.md"])


if __name__ == "__main__":
    unittest.main()
