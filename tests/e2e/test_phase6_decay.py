import json
import os
import time

from tests.e2e import harness
from tests.e2e.test_phase2_cli import Case
from tests.e2e.test_phase2_ui import tree_hash
from tests.support import e2e

FACT = "---\ncreated: 2026-01-01\nupdated: 2026-01-01\n---\n# {title}\n\nThe {title} fact.\n"


@e2e
class UsageTraceAndDecayReport(Case):
    def test_a_read_is_traced_and_the_report_lists_only_the_unread_file(self) -> None:
        read = self.put("knowledge/read-me.md", FACT.format(title="read-me"))
        self.put("knowledge/leave-me.md", FACT.format(title="leave-me"))
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write("\n[decay]\nwindow_days = 1\n")
        seed = {"t": time.time() - 2 * 86400, "sid8": "seed0000", "rel": "x.md", "ev": "read"}
        os.makedirs(self.sb.state, exist_ok=True)
        path = os.path.join(self.sb.state, "usage.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(seed) + "\n")

        self.go(f"Use the Read tool on {read} and then reply with the word done.", allow=["Read"])
        with open(path, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        reads = [r for r in rows if r["rel"] == "knowledge/read-me.md"]
        self.assertEqual([r["ev"] for r in reads], ["read"], rows)

        before = tree_hash(self.sb.vault)
        done = harness.shim(self.sb, "decay", "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual([c["path"] for c in report["candidates"]], ["knowledge/leave-me.md"])
        self.assertEqual(tree_hash(self.sb.vault), before)
