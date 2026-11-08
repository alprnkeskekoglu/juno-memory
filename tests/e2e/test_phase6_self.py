import json
import os
import shutil

from tests.e2e.test_phase2_cards import EDIT, CardE2E
from tests.e2e.test_phase2_hooks import Case
from tests.support import e2e

HEADER = "Juno model of the user"
QUOTE = "I would rather ship the small fix today and refactor the module next week"
WRAPPER = """#!{python}
import json, os, subprocess, sys
prompt = sys.stdin.read()
done = subprocess.run(
    [os.environ["WRAP_CLAUDE"], *sys.argv[1:]],
    input=prompt, capture_output=True, text=True, env=os.environ,
)
data = json.loads(done.stdout)
head = data.get("result", "").split("---JUNO-EXTRACT---")[0].rstrip()
extract = {{"self": [
    {{"file": "values", "text": "Prefers the small fix now over the refactor when time is short",
      "quote": os.environ["WRAP_QUOTE"]}},
    {{"file": "values", "text": "Prefers a quote that was never typed in this segment",
      "quote": "this sentence was never typed by anybody in the whole segment"}},
]}}
data["result"] = head + "\\n---JUNO-EXTRACT---\\n" + json.dumps(extract)
print(json.dumps(data))
"""


def put_arm(state: str, fraction: float) -> None:
    arms = os.path.join(state, "arms")
    os.makedirs(arms)
    body = {
        "name": "self",
        "salt": "e2e",
        "status": "running",
        "started": 1,
        "prereg": {"assignment": {"fraction": fraction}},
    }
    with open(os.path.join(arms, "self.json"), "w", encoding="utf-8") as handle:
        json.dump(body, handle)


@e2e
class SelfBlockOnArmOnly(Case):
    def start_arm(self, fraction: float) -> str:
        token = self.token("SELF")
        put_arm(self.sb.state, fraction)
        self.write(
            "self/values.md",
            f'# Values\n\n- Prefers {token} over the alternative · 2026-11-01 · src: user abcd1234 · "{QUOTE}"\n',
        )
        return token

    def test_session_start_has_the_block_on_arm(self) -> None:
        token = self.start_arm(1.0)
        run = self.go("Reply with the single word: ok")
        (text,) = self.contexts(run, "SessionStart", HEADER)
        self.assertIn(token, text)
        self.assertIn(QUOTE, text)
        self.assertNotIn("abcd1234", text)

    def test_session_start_has_no_block_off_arm(self) -> None:
        self.start_arm(0.0)
        run = self.go("Reply with the single word: ok")
        self.assertEqual(self.contexts(run, "SessionStart", HEADER), [])

    def test_a_subagent_never_gets_the_block(self) -> None:
        token = self.start_arm(1.0)
        prompt = (
            "Use the Agent tool exactly once with subagent_type general-purpose and this prompt: "
            "'Reply with the single word ok.' Then reply with the single word done."
        )
        run = self.go(prompt, allow=["Agent", "Task"])
        self.assertTrue(self.contexts(run, "SessionStart", HEADER), run.raw[:1500])
        for text in self.contexts(run, "SubagentStart"):
            self.assertNotIn(token, text)
            self.assertNotIn(HEADER, text)


@e2e
class SelfLineExtracted(CardE2E):
    def test_quoted_line_is_written_and_a_fabricated_quote_is_dropped_alone(self) -> None:
        self.extra_env.update(
            JUNO_CARD_CLAUDE=self.script("wrap-claude", WRAPPER),
            WRAP_CLAUDE=shutil.which("claude") or "claude",
            WRAP_QUOTE=QUOTE,
        )
        repo = self.repo()
        self.go(f"{QUOTE}. {EDIT.format(repo=repo)}", cwd=repo)
        (row,) = self.wait_runs(1)
        self.assertEqual(row["sections"]["self"], "ok", row)
        self.assertEqual(row["self"], {"written": ["values"], "dropped": {"unverified": 1}})
        text = self.read_vault("self/values.md")
        self.assertIn("Prefers the small fix now over the refactor when time is short", text)
        self.assertRegex(text, r'src: user \S{8} · "' + QUOTE)
        self.assertNotIn("never typed", text)


@e2e
class TurnLabelsInRunRecord(CardE2E):
    def test_run_record_has_integer_turn_counts(self) -> None:
        repo = self.repo()
        self.go(
            "Propose two options for naming the output file and recommend one, then use the "
            f"Write tool to write the text hello into {repo}/out.txt, then reply with the single "
            "word: done",
            cwd=repo,
        )
        (row,) = self.wait_runs(1)
        self.assertEqual(row["status"], "ok", row)
        self.assertEqual(row["sections"]["turns"], "ok", row)
        turns = row["turns"]
        self.assertEqual(sorted(turns), ["proposals", "pushback"], row)
        self.assertTrue(all(type(v) is int and v >= 0 for v in turns.values()), turns)

    def test_arm_run_labels_every_turn_and_reports_how_many_it_saw(self) -> None:
        put_arm(self.sb.state, 0.0)
        repo = self.repo()
        self.go(
            "Propose two options for naming the output file and recommend one, then use the "
            f"Write tool to write the text hello into {repo}/out.txt, then reply with the single "
            "word: done",
            cwd=repo,
        )
        (row,) = self.wait_runs(1)
        self.assertEqual(row["sections"]["turns"], "ok", row)
        self.assertGreaterEqual(row["turns_total"], 1, row)
        self.assertLessEqual(row["turns_seen"], row["turns_total"], row)
        self.assertLessEqual(row["turns"]["pushback"], row["turns"]["proposals"], row)
        self.assertLessEqual(row["turns"]["proposals"], row["turns_seen"], row)
