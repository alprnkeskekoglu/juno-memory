import shutil

from tests.e2e.test_phase2_cards import EDIT, CardE2E
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
extract = {{"procedures": [{{
    "slug": "write-out-file",
    "title": "Write and verify out.txt",
    "steps": ["Write the text hello into out.txt", "Check that out.txt exists in the repo"],
    "triggers": ["procmarker"],
    "repos": ["alpha"],
}}]}}
data["result"] = head + "\\n---JUNO-EXTRACT---\\n" + json.dumps(extract)
print(json.dumps(data))
"""


@e2e
class ProcedureExtractedAndTriggered(CardE2E):
    def test_extracted_procedure_is_written_and_injected_on_a_bash_trigger(self) -> None:
        self.extra_env.update(
            JUNO_CARD_CLAUDE=self.script("wrap-claude", WRAPPER),
            WRAP_CLAUDE=shutil.which("claude") or "claude",
        )
        repo = self.repo()
        self.go(EDIT.format(repo=repo), cwd=repo)
        (row,) = self.wait_runs(1)
        self.assertEqual(self.sections(row)["procedures"], "ok", row)
        text = self.read_vault("procedures/write-out-file.md")
        self.assertIn("triggers: [procmarker]", text)
        self.assertIn("1. Write the text hello into out.txt", text)

        run = self.go(
            "With the Bash tool run echo on the single word made of 'proc' directly followed by "
            "'marker' (no space). Type that word out as one unquoted word in the command, "
            "never as two quoted pieces. Then reply done",
            cwd=self.work,
            allow=["Bash"],
        )
        injected = "\n".join(self.contexts(run, "PreToolUse", "[Procedure ·"))
        self.assertIn(
            "[Procedure · procedures/write-out-file.md] Write and verify out.txt", injected
        )
        self.assertIn("2. Check that out.txt exists in the repo", injected)

    def sections(self, row: dict) -> dict:
        return dict(row["sections"])
