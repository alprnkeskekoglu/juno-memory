import json
import os
import re
import shutil
import subprocess

from juno.adapters import indexquery
from tests.e2e import harness
from tests.e2e.test_phase2_cards import EDIT, CardE2E
from tests.e2e.test_phase2_cli import Case
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
extract = {{"keys": [
    "The quokkaglyph marker was written to out.txt",
    "Search for quokkaglyph to find the out.txt edit",
]}}
data["result"] = head + "\\n---JUNO-EXTRACT---\\n" + json.dumps(extract)
print(json.dumps(data))
"""
WORD_RE = re.compile(r"[a-z][a-z0-9]{4,}")
NOTES = {
    "notes/rollout.md": (
        "# Rollout\n\nThe canary rollout of the checkout service waits for the platform lead to "
        "approve the blue switch before the pods are replaced. If the readiness probe fails twice "
        "the rollout stops and the previous replica set keeps serving traffic until someone "
        "reverts the release by hand.\n"
    ),
    "notes/billing.md": (
        "# Billing\n\nThe nightly billing job reads every refund of the day, rounds each amount "
        "to the currency's minor unit and writes one ledger row per refund. A refund that rounds "
        "to zero is skipped and logged, so the totals in the finance report can differ by a cent "
        "from the payment provider's statement.\n"
    ),
    "notes/onboarding.md": (
        "# Onboarding\n\nA new partner receives a welcome email, a sandbox workspace and a short "
        "checklist. The account manager books the kickoff call within two working days and the "
        "support team tags the partner so that early questions are answered by the same person "
        "until the first campaign has been launched.\n"
    ),
}


@e2e
class CardRunWritesFactKeys(CardE2E):
    def keys(self) -> list[dict[str, object]]:
        path = os.path.join(self.sb.state, "keys.jsonl")
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines()]

    def test_facts_are_stored_and_a_fact_only_term_finds_the_card_with_raw_text(self) -> None:
        self.extra_env.update(
            JUNO_CARD_CLAUDE=self.script("wrap-claude", WRAPPER),
            WRAP_CLAUDE=shutil.which("claude") or "claude",
        )
        repo = self.repo()
        self.go(EDIT.format(repo=repo), cwd=repo)
        (row,) = self.wait_runs(1)
        self.assertEqual((row["status"], row["error"]), ("ok", None), row)
        self.assertEqual(row["sections"]["keys"], "ok", row)
        stored = self.keys()
        self.assertTrue(stored)
        for item in stored:
            self.assertEqual(item["by"], "card")
            self.assertEqual(item["rel"], "handoffs/alpha.md")
            self.assertRegex(str(item["h"]), r"^[0-9a-f]{16}$")
        raw = stored[0]["facts"]
        assert isinstance(raw, list)
        facts = [str(f) for f in raw]
        self.assertTrue(2 <= len(facts) <= 5 or len(facts) == 1, facts)
        card = self.read_vault("handoffs/alpha.md").casefold()
        first = harness.shim(self.sb, "search", "hello", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        db = os.path.join(self.sb.state, "index.sqlite3")
        terms = [w for f in facts for w in WORD_RE.findall(f.casefold()) if w not in card]
        found = [
            t
            for t in dict.fromkeys(terms)
            if not indexquery.search(db, t, keys_weight=0.0)
            and indexquery.search(db, t, keys_weight=2.0)
        ]
        self.assertTrue(found, f"no fact-only term in {facts}")
        done = harness.shim(self.sb, "search", found[0], "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        hit = json.loads(done.stdout)["results"][0]
        self.assertEqual(hit["relpath"], "handoffs/alpha.md")
        self.assertNotIn(found[0], hit["snippet"].casefold())


@e2e
class BackfillKeysResumes(Case):
    def juno(self, *argv: str) -> subprocess.CompletedProcess[str]:
        env = harness.claude_env(self.sb, False)
        return subprocess.run(
            [os.path.join(self.sb.bin, "juno"), *argv],
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=300,
        )

    def test_three_passages_are_keyed_and_the_second_run_calls_no_model(self) -> None:
        for rel, text in NOTES.items():
            self.put(rel, text)
        first = self.juno("keys", "backfill", "--max-usd", "0.2", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        out = json.loads(first.stdout)
        self.assertEqual((out["passages"], out["calls"], out["stopped"]), (3, 1, None), out)
        self.assertEqual((out["keyed"], out["dropped"]), (3, 0), out)
        self.assertTrue(0 < out["cost_usd"] <= 0.2, out)
        self.assertIn("batch 1/1", first.stderr)
        with open(os.path.join(self.sb.state, "keys.jsonl"), encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle.read().splitlines()]
        self.assertEqual(sorted(r["rel"] for r in rows), sorted(NOTES))
        self.assertTrue(all(r["by"] == "backfill" and len(r["facts"]) >= 1 for r in rows), rows)
        second = self.juno("keys", "backfill", "--max-usd", "0.2", "--json")
        self.assertEqual(second.returncode, 0, second.stderr)
        again = json.loads(second.stdout)
        self.assertEqual((again["calls"], again["pending"], again["cost_usd"]), (0, 0, 0.0))
        status = json.loads(self.juno("keys", "status", "--json").stdout)
        self.assertEqual((status["keyed"], status["pending"]), (3, 0))
