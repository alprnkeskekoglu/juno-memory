import json
import shutil
from pathlib import Path
from typing import Any

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
extract = {{"next": [
    {{"q": "Where did the hello text go?", "a": "Into out.txt in the alpha repo."}},
    {{"q": "Is out.txt checked in?", "a": "No, it is only written."}},
]}}
data["result"] = head + "\\n---JUNO-EXTRACT---\\n" + json.dumps(extract)
print(json.dumps(data))
"""


@e2e
class NextQuestionsStoredAndShownOnArm(CardE2E):
    def arm(self, fraction: float) -> None:
        directory = Path(self.sb.state, "arms")
        directory.mkdir(parents=True, exist_ok=True)
        body = {
            "name": "next",
            "salt": "e2e",
            "status": "running",
            "started": 1,
            "prereg": {"assignment": {"fraction": fraction}},
        }
        (directory / "next.json").write_text(json.dumps(body))

    def pack_events(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        directory = Path(self.sb.state, "ledgers")
        for path in sorted(directory.iterdir()):
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            found += [r for r in rows if r.get("ev") == "pack" and r["key"] == "repo:alpha"]
        return found

    def test_questions_are_stored_in_state_and_shown_only_to_the_on_arm(self) -> None:
        self.extra_env.update(
            JUNO_CARD_CLAUDE=self.script("wrap-claude", WRAPPER),
            WRAP_CLAUDE=shutil.which("claude") or "claude",
        )
        self.arm(1.0)
        repo = self.repo()
        self.go(EDIT.format(repo=repo), cwd=repo)
        (row,) = self.wait_runs(1)
        self.assertEqual(dict(row["sections"])["next"], "ok", row)
        stored = Path(self.sb.state, "cards", "next.jsonl").read_text()
        self.assertIn("Where did the hello text go?", stored)
        (card,) = self.cards("alpha")
        seg8 = str(card["seg"])
        self.assertIn(f'"seg": "{seg8}"', stored)
        self.assertNotIn("Where did the hello text go?", self.read_vault("handoffs/alpha.md"))
        self.assertNotIn("[Next", self.read_vault("handoffs/alpha.md"))

        on = self.go("Reply with the single word ok.", cwd=repo)
        text = "\n".join(self.contexts(on, "SessionStart", "[Next ·"))
        self.assertIn(f"[Next · {seg8}]\n- Where did the hello text go? -> Into out.txt", text)
        self.assertLessEqual(len(text.split(f"[Next · {seg8}]\n", 1)[1].split("\n[", 1)[0]), 400)

        self.arm(0.0)
        off = self.go("Reply with the single word ok.", cwd=repo)
        self.assertEqual(self.contexts(off, "SessionStart", "[Next ·"), [])
        events = [e for e in self.pack_events() if "next_shown" in e or "next_held" in e]
        self.assertEqual(
            [e.get("next_shown") or e.get("next_held") for e in events], [seg8, seg8], events
        )
        self.assertIn("next_shown", events[0])
        self.assertIn("next_held", events[1])
