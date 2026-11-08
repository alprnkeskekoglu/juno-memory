import json
import os

from tests.e2e.harness import JUNO_BIN
from tests.e2e.test_phase2_cli import Case, command
from tests.support import e2e

START = "<!-- juno:tickets:start -->"
END = "<!-- juno:tickets:end -->"


@e2e
class ProfilesRegenerateTicketsBlock(Case):
    def test_profiles_rewrites_only_the_tickets_block_and_undo_reverts_it(self) -> None:
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write('\n[jira]\nprefixes = ["DEF"]\n')
        self.put("tasks/DEF-1.md", "---\ntitle: Add widget\nstatus: open\nrepos: [alpha]\n---\n")
        before = "# alpha\n\n## Tickets\n\n## Gotchas\nkeep this line\n"
        self.put("projects/alpha.md", before)
        run = self.go(command(JUNO_BIN, "profiles"))
        out = self.output(run)
        self.assertIn("updated projects/alpha.md (write w-", out, run.raw[-2500:])
        text = self.get("projects/alpha.md")
        self.assertIn(f"{START}\n- [[tasks/DEF-1]] · open · Add widget\n{END}", text)
        self.assertIn("## Gotchas\nkeep this line\n", text)
        path = os.path.join(self.sb.state, "writes.jsonl")
        with open(path, encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle.read().splitlines()]
        self.assertEqual(
            [(r["verb"], r["file"]) for r in records], [("profiles", "projects/alpha.md")]
        )
        run = self.go(command(JUNO_BIN, "undo", str(records[0]["id"])))
        self.assertIn("reverted in projects/alpha.md", self.output(run), run.raw[-2500:])
        self.assertEqual(self.get("projects/alpha.md"), before)
