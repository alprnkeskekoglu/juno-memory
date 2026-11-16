import shutil

from tests.e2e.test_phase2_cards import EDIT
from tests.e2e.test_phase3_jira import JiraCase
from tests.support import e2e

WRAPPER = """#!{python}
import json, os, subprocess, sys
prompt = sys.stdin.read()
done = subprocess.run(
    [os.environ["WRAP_CLAUDE"], *sys.argv[1:]],
    input=prompt, capture_output=True, text=True, env=os.environ,
)
data = json.loads(done.stdout)
head = data.get("result", "").split("---JUNO-EXTRACT---")[0].rstrip()
token = os.environ["WRAP_TOKEN"]
extract = {{"ticket": [{{"key": "DEF-12", "lines": [
    {{"section": "Summary", "key": "summary", "evidence": token,
      "text": "The widget write path is replaced by one Write call, the plan marker is " + token}},
    {{"section": "Tests", "key": "outcome", "evidence": "/nowhere/in/the/digest",
      "text": "This line is not anchored in the digest so it must be dropped alone"}},
]}}]}}
data["result"] = head + "\\n---JUNO-EXTRACT---\\n" + json.dumps(extract)
print(json.dumps(data))
"""


@e2e
class TicketKindFillsTheTaskFile(JiraCase):
    def test_a_worked_ticket_gets_its_summary_and_the_unanchored_line_is_dropped(self) -> None:
        token = self.token("TICKET")
        self.configure(
            '[jira]\nprefixes = ["DEF"]\nbase_url = "https://jira.example.test"\n'
            '\n[handoff]\nmodel = "haiku"\n'
        )
        self.extra_env.update(
            JUNO_CARD_CLAUDE=self.script("wrap-claude", WRAPPER),
            WRAP_CLAUDE=shutil.which("claude") or "claude",
            WRAP_TOKEN=token,
        )
        repo = self.branch_repo()
        self.go(EDIT.format(repo=repo) + f" The plan marker for DEF-12 is {token}.", cwd=repo)
        (row,) = self.wait_runs(1)
        self.assertEqual(dict(row["sections"])["ticket"], "ok", row)
        text = self.read_vault("tasks/DEF-12.md")
        self.assertIn("digests: [Summary]", text)
        self.assertIn(
            f"- [summary] The widget write path is replaced by one Write call, the plan marker is {token}",
            text,
        )
        self.assertNotIn("not anchored", text)
        self.assertEqual(row["ticket"]["dropped"], {"unanchored": 1})
