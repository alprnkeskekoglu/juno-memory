import json
import os
import time

from juno.core import isoweek
from tests.e2e import harness
from tests.e2e.test_phase2_cli import Case
from tests.support import e2e
from tests.support.reportseed import seed

HEADERS = ("## Signals", "## Retrieval", "## Arms", "## Memory", "## Pending proposals")


@e2e
class WeeklyReportBuilt(Case):
    def projects(self) -> str:
        return os.path.join(self.sb.root, "claude-projects")

    def test_report_for_a_week_has_every_section_and_no_empty_signal(self) -> None:
        self.sb.env["JUNO_CLAUDE_PROJECTS"] = self.projects()
        seed(self.sb.state, self.sb.vault, "2026-W45", self.projects())
        done = harness.shim(self.sb, "report", "--week", "2026-W45", "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        text = self.get("_generated/reports/2026-W45.md")
        for header in HEADERS:
            self.assertIn(header, text)
        self.assertIn("corrections per 100 prompts: 50.0", text)
        self.assertIn("recall proxy (search only): 1/1", text)
        signals = json.loads(done.stdout)["signals"]
        self.assertEqual([k for k, v in signals.items() if v is None], [])

    def test_a_session_without_last_weeks_report_spawns_it_and_announces_it_once(self) -> None:
        label = isoweek.last_week(time.time())
        seed(self.sb.state, self.sb.vault, label, self.projects())
        target = self.vp(f"_generated/reports/{label}.md")
        self.assertFalse(os.path.exists(target))
        first = self.go("Reply with the single word: ok")
        deadline = time.time() + 30
        while not os.path.exists(target) and time.time() < deadline:
            time.sleep(0.5)
        self.assertTrue(os.path.exists(target), "the detached report run did not write the file")
        self.assertNotIn("weekly report", self.start_context(first))
        second = self.go("Reply with the single word: ok")
        self.assertIn(
            f"Juno: weekly report {label} ready: _generated/reports/{label}.md",
            self.start_context(second),
        )
        third = self.go("Reply with the single word: ok")
        self.assertNotIn("weekly report", self.start_context(third))

    def start_context(self, run: harness.Run) -> str:
        found = [
            harness.context_of(r) or ""
            for r in run.hook_responses
            if r.get("hook_event") == "SessionStart"
        ]
        return "\n".join(found)
