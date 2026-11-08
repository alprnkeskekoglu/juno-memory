import json
import os
import subprocess
import unittest

from tests.e2e import harness
from tests.support import REPO, Sandbox, e2e

SID = "57fb7685-9be8-4bc7-a435-47aa50ccd35f"
HOOK = os.path.join(REPO, "bin", "juno-hook")
QUESTION = {
    "question": "Which database should we use?",
    "header": "Database",
    "multiSelect": False,
    "options": [
        {"label": "SQLite", "description": "file based"},
        {"label": "Postgres (Recommended)", "description": "server"},
        {"label": "DuckDB", "description": "analytics"},
    ],
}


@e2e
class AskLogRecordsRecommendedPick(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        done = harness.init_with_bin(self.sb)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.env = dict(self.sb.env, JUNO_TEST_INTERACTIVE="1")
        self.work = os.path.join(self.sb.root, "work")
        os.makedirs(self.work)

    def fire(self, event: str, **fields: object) -> None:
        payload = {
            "session_id": SID,
            "cwd": self.work,
            "hook_event_name": event,
            "tool_name": "AskUserQuestion",
            "tool_use_id": "toolu_01AbCdEfGh",
            **fields,
        }
        done = subprocess.run(
            [HOOK, event],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=self.env,
            timeout=30,
        )
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_answered_question_appends_a_row_without_text(self) -> None:
        tool_in = {"questions": [QUESTION]}
        self.fire("pre_tool_use", tool_input=tool_in)
        self.fire(
            "post_tool_use",
            tool_input=tool_in,
            tool_response={"answers": {QUESTION["question"]: "Postgres (Recommended)"}},
        )
        path = os.path.join(self.sb.state, "asks.jsonl")
        with open(path, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle.read().splitlines()]
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual(
            {
                k: row[k]
                for k in ("sid", "tid8", "q", "n_options", "rec", "picked", "other", "multi")
            },
            {
                "sid": SID,
                "tid8": "toolu_01",
                "q": 0,
                "n_options": 3,
                "rec": 1,
                "picked": [1],
                "other": False,
                "multi": False,
            },
        )
        self.assertNotIn("Postgres", json.dumps(row))
        self.assertNotIn("database", json.dumps(row))


if __name__ == "__main__":
    unittest.main()
