import datetime
import os
from typing import Any

from tests.e2e import harness
from tests.e2e.test_phase2_cli import Case
from tests.support import e2e

TARGET = "notes/midnight-ferry.md"
NOTE = """---
kind: note
---

# Retiring the midnight ferry

## Symptom

Every run of the midnight ferry failed with code QX-4471, so it was switched off.

## Vendor ceiling

The vendor throttles at 37 requests per minute and the ferry sent far more. Pull-based
sync every 15 minutes replaced it.
"""
QUESTION = "Why did we turn off the nightly upload job? I only remember an error code, QX-4471."


def tool_calls(run: harness.Run) -> list[dict[str, Any]]:
    calls = []
    for event in run.events:
        if event.get("type") != "assistant":
            continue
        for block in event.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append(block)
    return calls


@e2e
class AgenticSearchReformulates(Case):
    def test_juno_skill_varies_searches_reads_the_vault_and_cites_the_path(self) -> None:
        self.put(TARGET, NOTE)
        for i in range(4):
            self.put(f"notes/filler-{i}.md", f"# Filler {i}\n\n## Misc\n\nUnrelated text {i}.\n")
        env = self.env(PATH=self.sb.bin + os.pathsep + os.environ["PATH"])
        run = harness.run_claude(
            f"/juno {QUESTION}",
            env,
            model="sonnet",
            timeout=300,
            cwd=self.work,
            extra=["--permission-mode", "acceptEdits"],
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        calls = tool_calls(run)
        searches = [
            c
            for c in calls
            if c["name"] == "Bash" and "juno search" in c["input"].get("command", "")
        ]
        greps = [c for c in calls if c["name"] == "Grep"]
        self.assertTrue(len(searches) >= 2 or greps, [c["name"] for c in calls])
        reads = [
            c for c in calls if c["name"] == "Read" and TARGET in c["input"].get("file_path", "")
        ]
        self.assertTrue(reads, [c["input"] for c in calls])
        self.assertIn("37", run.result)
        self.assertIn(TARGET, run.result)
        self.assertLessEqual(len(calls), 7, [c["name"] for c in calls])


@e2e
class AgenticSearchUsesTimeFilter(Case):
    def test_a_question_naming_last_week_gets_a_since_filter(self) -> None:
        today = datetime.date.today()
        recent = today - datetime.timedelta(days=6)
        old = today - datetime.timedelta(days=60)
        self.put(
            "handoffs/recent.md",
            f"# H\n\n## {recent} 09:00 · - · c · aaaaaaa1\nWe chose the quartz scheduler for nightly sync.\n",
        )
        self.put(
            "handoffs/old.md",
            f"# H\n\n## {old} 09:00 · - · c · aaaaaaa2\nWe chose the cron scheduler for nightly sync.\n",
        )
        env = self.env(PATH=self.sb.bin + os.pathsep + os.environ["PATH"])
        run = harness.run_claude(
            f"/juno what did we decide last week about the nightly sync scheduler? Today is {today}.",
            env,
            model="sonnet",
            timeout=300,
            cwd=self.work,
            extra=["--permission-mode", "acceptEdits"],
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        searches = [
            c["input"]["command"]
            for c in tool_calls(run)
            if c["name"] == "Bash" and "juno search" in c["input"].get("command", "")
        ]
        self.assertTrue(any("--since" in c for c in searches), searches)
        self.assertIn("quartz", run.result.lower())


if __name__ == "__main__":
    import unittest

    unittest.main()
