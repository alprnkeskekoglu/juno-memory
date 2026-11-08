import os
import subprocess

from juno import config
from juno.adapters import ledgerio
from juno.core import vaultfmt
from juno.core.ledger import Segment
from tests.e2e import harness
from tests.e2e.test_phase2_cards import CARD_CLAUDE, CardE2E
from tests.support import e2e

BRANCH = "feature/DEF-12-x"
PROMPT = (
    "You have no Jira tool; the user gives you the ticket data: title 'Add widget', status "
    "'In Review'. First do what the Juno session context says about the ticket's task file "
    "(if it says nothing, run `juno task set DEF-12 --title 'Add widget' --status 'In Review'` "
    "once and do not retry whatever it prints). Then use the Write tool to write the text hello "
    "into {repo}/out.txt, then reply with the single word: done"
)


@e2e
class JiraCase(CardE2E):
    def env(self, interactive: bool = True) -> dict[str, str]:
        env = super().env(interactive)
        env["PATH"] = self.sb.bin + os.pathsep + env.get("PATH", "")
        return env

    def branch_repo(self) -> str:
        repo = self.repo()
        subprocess.run(
            ["git", "-C", repo, "symbolic-ref", "HEAD", f"refs/heads/{BRANCH}"], check=True
        )
        return repo

    def session(self, toml: str) -> harness.Run:
        self.configure(toml + '\n[handoff]\nmodel = "haiku"\n')
        self.extra_env["JUNO_CARD_CLAUDE"] = self.script("card-claude", CARD_CLAUDE)
        repo = self.branch_repo()
        run = self.go(PROMPT.format(repo=repo), cwd=repo, allow=["Bash"])
        self.assertEqual(self.ended().get("skip"), None, run.raw[-1500:])
        self.wait_runs(1)
        return run

    def segment(self) -> Segment:
        return ledgerio.fold(config.load(self.env()), self.session_id())


@e2e
class TicketDetectedAndTaskFileWritten(JiraCase):
    def test_branch_ticket_pack_hint_and_juno_task_set(self) -> None:
        run = self.session('[jira]\nprefixes = ["DEF"]\nbase_url = "https://jira.example.test"')
        start = "\n".join(self.contexts(run, "SessionStart"))
        self.assertIn("=== Juno pack: ticket DEF-12 ===", start)
        self.assertIn("juno task set DEF-12", start)
        self.assertEqual(self.ledger()[0]["repos"], [["alpha", "DEF-12"]])
        text = self.read_vault("tasks/DEF-12.md")
        self.assertTrue(text, run.raw[-2500:])
        fields, _ = vaultfmt.parse_frontmatter(text)
        self.assertEqual(fields["jira"], "DEF-12")
        self.assertEqual(vaultfmt.field_str(fields, "status"), "in-review")
        self.assertIn("widget", str(fields["title"]).lower())
        self.assertEqual(fields["url"], "https://jira.example.test/browse/DEF-12")
        self.assertTrue(fields["updated"])
        self.assertEqual(self.segment().current_ticket(), "DEF-12")
        (card,) = self.cards("DEF-12")
        self.assert_card(card, self.session_id())
        self.assertEqual(self.cards("alpha"), [])


@e2e
class WithoutJiraNoTicketAndRoutesByRepo(JiraCase):
    def test_same_branch_yields_no_ticket_and_the_card_goes_to_the_repo_file(self) -> None:
        run = self.session("")
        start = "\n".join(self.contexts(run, "SessionStart"))
        self.assertNotIn("Juno pack: ticket", start)
        self.assertNotIn("juno task set", start)
        self.assertEqual(self.ledger()[0]["repos"], [["alpha", None]])
        self.assertEqual(list(self.segment().tickets), [])
        self.assertIsNone(self.segment().current_ticket())
        self.assertEqual(self.read_vault("tasks/DEF-12.md"), "")
        self.assertEqual(self.cards("DEF-12"), [])
        (card,) = self.cards("alpha")
        self.assert_card(card, self.session_id())
