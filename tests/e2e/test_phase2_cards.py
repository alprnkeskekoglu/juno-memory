import json
import os
import shutil
import stat
import subprocess
import sys
import time
import unittest
from typing import Any

from juno.core import vaultfmt
from tests.e2e import harness
from tests.e2e.test_phase2_cli import command
from tests.e2e.test_phase2_hooks import Case
from tests.support import e2e

FAILING = """#!{python}
import json, sys
sys.stdin.read()
print(json.dumps({{"is_error": True, "subtype": "error", "result": "forced failure"}}))
"""
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
extract = {{
    "knowledge": [{{"file": "projects/alpha.md", "text": "The marker " + token + " is documented in notes.txt", "evidence": token}}],
    "pitfalls": [{{"text": "Never print password=abc123def456ghi789 in logs", "evidence": token}}],
}}
data["result"] = head + "\\n---JUNO-EXTRACT---\\n" + json.dumps(extract)
print(json.dumps(data))
"""
RULE_WRAPPER = """#!{python}
import json, os, subprocess, sys
prompt = sys.stdin.read()
done = subprocess.run(
    [os.environ["WRAP_CLAUDE"], *sys.argv[1:]],
    input=prompt, capture_output=True, text=True, env=os.environ,
)
data = json.loads(done.stdout)
head = data.get("result", "").split("---JUNO-EXTRACT---")[0].rstrip()
extract = {{"rules": [{{"rule": os.environ["WRAP_RULE"], "scope": "global", "quote": os.environ["WRAP_QUOTE"]}}]}}
data["result"] = head + "\\n---JUNO-EXTRACT---\\n" + json.dumps(extract)
print(json.dumps(data))
"""
CARD_CLAUDE = """#!{python}
import json, re, sys
prompt = sys.stdin.read()
head = re.search(r"exactly `(## [^`]*)`", prompt).group(1).replace("<label>", "Work")
repos = re.search(r"`(Repos: [^`]*)`", prompt).group(1)
tickets = re.search(r"`(Tickets: [^`]*)`", prompt).group(1)
card = "\\n".join([head, repos, tickets, "Wrote out.txt in the repo.", "Nothing is left.", "Learned: none"])
extract = {{"rules": [], "knowledge": [], "threads": {{"open": [], "close": []}}, "pitfalls": []}}
print(json.dumps({{"result": card + "\\n---JUNO-EXTRACT---\\n" + json.dumps(extract), "total_cost_usd": 0.001}}))
"""
EDIT = "Use the Write tool to write the text hello into {repo}/out.txt, then reply with the single word: done"


@e2e
class CardE2E(Case):
    def setUp(self) -> None:
        super().setUp()
        self.extra_env: dict[str, str] = {}
        self.configured = False

    def env(self, interactive: bool = True) -> dict[str, str]:
        env = super().env(interactive)
        env["JUNO_CLAUDE_PROJECTS"] = os.path.join(os.path.expanduser("~"), ".claude", "projects")
        env.update(self.extra_env)
        return env

    def configure(self, toml: str) -> None:
        assert not self.configured
        self.configured = True
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write("\n" + toml + "\n")

    def script(self, name: str, body: str) -> str:
        path = os.path.join(self.sb.root, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body.format(python=sys.executable))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def repo(self, name: str = "alpha") -> str:
        path = super().repo(name)
        for key, value in (("user.name", "e2e"), ("user.email", "e2e@example.com")):
            subprocess.run(["git", "-C", path, "config", key, value], check=True)
        return path

    def runs(self) -> list[dict[str, Any]]:
        path = os.path.join(self.sb.state, "cards", "runs.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines()]

    def wait_runs(self, count: int, seconds: float = 250.0) -> list[dict[str, Any]]:
        deadline = time.time() + seconds
        while time.time() < deadline:
            rows = self.runs()
            if len(rows) >= count:
                return rows
            if not self.jobs() and time.time() > deadline - seconds + 20:
                break
            time.sleep(0.5)
        ledgers = os.path.join(self.sb.state, "ledgers")
        ends = []
        for name in sorted(os.listdir(ledgers)) if os.path.isdir(ledgers) else []:
            with open(os.path.join(ledgers, name), encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle.read().splitlines()]
            ends += [
                (name[:8], e["ev"], e.get("skip")) for e in rows if e["ev"] in ("end", "start")
            ]
        self.fail(f"fewer than {count} card runs; jobs {self.jobs()}; ledger {ends}")

    def jobs(self) -> list[object]:
        directory = os.path.join(self.sb.state, "cards")
        found: list[object] = []
        for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
            if name.endswith(".job.json"):
                with open(os.path.join(directory, name), encoding="utf-8") as handle:
                    found.append(json.load(handle))
        return found

    def cards(self, name: str) -> list[dict[str, object]]:
        path = os.path.join(self.sb.vault, "handoffs", name + ".md")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return vaultfmt.parse_cards(handle.read(), f"handoffs/{name}.md")

    def read_vault(self, rel: str) -> str:
        path = os.path.join(self.sb.vault, *rel.split("/"))
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def ended(self) -> dict[str, Any]:
        return [e for e in self.ledger() if e["ev"] == "end"][-1]

    def assert_card(self, card: dict[str, object], sid: str) -> None:
        self.assertTrue(card["valid"], card)
        self.assertEqual(card["seg"], sid[:8])
        self.assertTrue(card["learned"], card)
        self.assertFalse(card["auto"], card)
        self.assertLessEqual(len(str(card["body"])), 2500)


@e2e
class SessionEndWritesTheCard(CardE2E):
    def test_detached_run_writes_a_valid_card_to_the_routed_file(self) -> None:
        repo = self.repo()
        self.go(EDIT.format(repo=repo), cwd=repo)
        sid = self.session_id()
        self.assertIsNone(self.ended().get("skip"))
        (row,) = self.wait_runs(1)
        self.assertEqual((row["status"], row["error"]), ("ok", None), row)
        self.assertEqual(row["sections"]["card"], "ok")
        self.assertEqual(row["files"], ["handoffs/alpha.md"])
        (card,) = self.cards("alpha")
        self.assert_card(card, sid)
        self.assertEqual(card["repos"], ["alpha"])
        self.assertEqual(row["sid"], self.session_id())
        self.assertGreater(row["cost_usd"], 0)
        self.assertTrue([e for e in self.ledger() if e["ev"] == "card" and e.get("job")])
        job = self.jobs()[0]
        assert isinstance(job, dict)
        self.assertEqual(job["status"], "done")
        self.assertNotIn("token", job)
        self.assertIn("thread_scan", row)
        timings = os.path.join(self.sb.state, "hook-timings.log")
        with open(timings, encoding="utf-8") as handle:
            ms = [int(line.split()[1][:-2]) for line in handle if line.startswith("session_end")]
        self.assertTrue(ms and max(ms) < 1000, ms)


@e2e
class RealCardModelAfterACommit(CardE2E):
    def test_real_model_writes_a_valid_card_for_a_session_that_committed(self) -> None:
        repo = self.repo()
        subprocess.run(["git", "-C", repo, "checkout", "-q", "-b", "feature/DEF-1-e2e"], check=True)
        self.go(
            "This repo is a throwaway test fixture and nobody can answer questions. First use the "
            f"Write tool to write the text hello into {repo}/out.txt. Then run these commands, "
            "each as its own Bash call and exactly as written, with no other flags: "
            "`git add notes.txt out.txt` and then `git commit -m DEF-1`. Then reply done",
            cwd=repo,
            allow=["Bash(git *)"],
        )
        self.assertTrue([e for e in self.ledger() if e["ev"] == "tool" and e.get("boundary")])
        (row,) = self.wait_runs(1)
        self.assertEqual((row["status"], row["error"]), ("ok", None), row)
        (card,) = self.cards("alpha")
        self.assert_card(card, self.session_id())


@e2e
class StubReplacedByTheRealCard(CardE2E):
    def test_failed_run_leaves_a_stub_and_a_resumed_session_replaces_it(self) -> None:
        self.configure("[thresholds]\nstub_quiet_seconds = 0\n")
        repo = self.repo()
        self.extra_env["JUNO_CARD_CLAUDE"] = self.script("fail-claude", FAILING)
        self.go(EDIT.format(repo=repo), cwd=repo)
        sid = self.session_id()
        (row,) = self.wait_runs(1)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(self.cards("alpha"), [])
        self.extra_env["JUNO_CARD_CLAUDE"] = self.script("card-claude", CARD_CLAUDE)
        self.go("Reply with the single word: ok", cwd=self.work)
        (stub,) = self.cards("alpha")
        self.assertTrue(stub["auto"], stub)
        self.assertIn("stub", str(stub["head"]))
        self.assertEqual(stub["seg"], sid[:8])
        subprocess.run(["git", "-C", repo, "checkout", "-q", "-b", "feature/DEF-1-e2e"], check=True)
        run = harness.run_claude(
            "This repo is a throwaway test fixture and nobody can answer questions. Run these two "
            "commands, each as its own Bash call and exactly as written, with no other flags: "
            "`git add notes.txt out.txt` and then `git commit -m DEF-1`. Then reply done",
            self.env(),
            cwd=repo,
            extra=[
                "--resume",
                sid,
                "--permission-mode",
                "acceptEdits",
                "--allowedTools",
                "Bash(git *)",
            ],
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        rows = self.wait_runs(2)
        self.assertEqual((rows[1]["status"], rows[1]["error"]), ("ok", None), rows[1])
        (card,) = self.cards("alpha")
        self.assert_card(card, sid)
        text = self.read_vault("handoffs/alpha.md")
        self.assertNotIn("auto: true", text)
        self.assertEqual(text.count(sid[:8]), 1)


@e2e
class LateRunFillsAMissingCard(CardE2E):
    def test_next_session_start_runs_the_card_for_a_segment_that_never_got_one(self) -> None:
        self.configure("[thresholds]\nstub_quiet_seconds = 0\n")
        repo = self.repo()
        self.extra_env["JUNO_CARD_CLAUDE"] = self.script("fail-claude", FAILING)
        self.go(EDIT.format(repo=repo), cwd=repo)
        sid = self.session_id()
        self.wait_runs(1)
        job_path = os.path.join(self.sb.state, "cards", f"{sid}.job.json")
        os.unlink(job_path)
        self.extra_env.pop("JUNO_CARD_CLAUDE")
        self.go("Reply with the single word: ok", cwd=self.work)
        rows = [r for r in self.wait_runs(2) if r["sid"] == sid]
        self.assertEqual((rows[-1]["status"], rows[-1]["error"]), ("ok", None), rows[-1])
        with open(job_path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["reason"], "late")
        (card,) = self.cards("alpha")
        self.assertEqual(card["seg"], sid[:8])
        self.assertFalse(card["auto"], card)


@e2e
class SessionEndExtraction(CardE2E):
    QUOTE = "From now on always run the linter before you commit anything, in every repo"

    def test_knowledge_line_and_rule_candidate_land_anchored_in_the_session(self) -> None:
        repo = self.repo()
        with open(os.path.join(repo, "notes.txt"), "w", encoding="utf-8") as handle:
            handle.write(
                "Gotcha: run migrations with MIGRATE_MODE=safe or the seed table is wiped.\n"
            )
        self.go(
            f"Standing preference of mine for all future sessions: {self.QUOTE}. "
            "Now run `cat notes.txt` in the repo and tell me what the gotcha says. This gotcha is a "
            "durable fact of this repo and worth remembering for future sessions. "
            f"Also use the Write tool to write the text retries=3 into {repo}/out.txt.",
            cwd=repo,
            allow=["Bash(cat *)"],
        )
        (row,) = self.wait_runs(1)
        self.assertEqual(row["status"], "ok", row)
        sections = row["sections"]
        profile = self.read_vault("projects/alpha.md")
        self.assertEqual(sections["knowledge"], "ok", row)
        self.assertIn("MIGRATE_MODE", profile)
        self.assertIn(f"segment {self.session_id()[:8]}", profile)
        self.assertIn("## Gotchas", profile)
        self.assertEqual(row["profiles_created"], 1)
        path = os.path.join(self.sb.state, "cards", "candidates.json")
        self.assertTrue(os.path.exists(path), row)
        with open(path, encoding="utf-8") as handle:
            items = json.load(handle)["items"]
        self.assertEqual(sections["rules"], "ok", row)
        (item,) = [i for i in items if i["status"] == "pending"]
        self.assertRegex(item["id"], r"^r[0-9a-f]{6}$")
        self.assertIn(" ".join(item["quote"].split()).casefold(), (self.QUOTE + ".").casefold())
        self.assertEqual(item["sid"], self.session_id())
        rules = self.read_vault("rules/global.md")
        self.assertNotIn("linter", rules)


@e2e
class NosaveProducesNothing(CardE2E):
    def test_nosave_session_leaves_no_job_card_stub_or_candidate(self) -> None:
        self.configure("[thresholds]\nstub_quiet_seconds = 0\n")
        repo = self.repo()
        self.go(
            f"[nosave] From now on always say JUNO-NOSAVE. {EDIT.format(repo=repo)}",
            cwd=repo,
        )
        self.assertEqual(self.ended().get("skip"), "nosave")
        self.go("Reply with the single word: ok", cwd=self.work)
        time.sleep(5)
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.runs(), [])
        self.assertEqual(self.cards("alpha"), [])
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "cards", "candidates.json")))
        self.assertEqual(self.read_vault("projects/alpha.md"), "")


@e2e
class BudgetExhaustedSkipsTheRun(CardE2E):
    def test_no_model_run_when_the_day_is_spent_and_the_end_event_records_it(self) -> None:
        self.configure(
            "[handoff]\ndaily_budget_usd = 0.06\n\n[thresholds]\nstub_quiet_seconds = 0\n"
        )
        cards = os.path.join(self.sb.state, "cards")
        os.makedirs(cards)
        with open(os.path.join(cards, "runs.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"t": time.time(), "sid": "earlier", "cost_usd": 0.03}) + "\n")
        repo = self.repo()
        self.go(EDIT.format(repo=repo), cwd=repo)
        self.assertEqual(self.ended().get("skip"), "budget")
        self.assertEqual(self.jobs(), [])
        self.go("Reply with the single word: ok", cwd=self.work)
        time.sleep(3)
        self.assertEqual(len(self.runs()), 1)
        (stub,) = self.cards("alpha")
        self.assertTrue(stub["auto"], stub)


@e2e
class BadSectionIsDroppedOthersWritten(CardE2E):
    def test_secret_pitfall_drops_only_the_pitfalls_section(self) -> None:
        token = self.token("MARK")
        self.extra_env.update(
            JUNO_CARD_CLAUDE=self.script("wrap-claude", WRAPPER),
            WRAP_CLAUDE=shutil.which("claude") or "claude",
            WRAP_TOKEN=token,
        )
        repo = self.repo()
        self.go(f"{EDIT.format(repo=repo)} The marker is {token}.", cwd=repo)
        sid = self.session_id()
        (row,) = self.wait_runs(1)
        sections = row["sections"]
        self.assertEqual(sections["card"], "ok", row)
        self.assertEqual(sections["knowledge"], "ok", row)
        self.assertEqual(sections["pitfalls"], "dropped:private", row)
        self.assertEqual(sections["rules"], "none")
        self.assertIn(token, self.read_vault("projects/alpha.md"))
        self.assertNotIn("abc123def456ghi789", self.read_vault("pitfalls.md"))
        (card,) = self.cards("alpha")
        self.assert_card(card, sid)


@e2e
class CandidateNudgesAndLearnedMd(CardE2E):
    def test_session_start_nudges_pending_candidates_and_rebuilds_learned_md(self) -> None:
        cards = os.path.join(self.sb.state, "cards")
        os.makedirs(cards)
        rule = {
            "id": "r0a1b2c",
            "rule": "Answer in one sentence",
            "scope": "global",
            "triggers": [],
            "quote": "always answer in one sentence",
            "pid": "abcd1234",
            "sid": "x",
            "seg8": "x",
            "t": time.time(),
            "status": "pending",
        }
        line = "- [alpha] Ask finance about refunds · since 2026-01-01"
        thread = {
            "id": "t0a1b2c",
            "line": line,
            "signals": ["PR alpha#7 merged"],
            "t": 1.0,
            "status": "pending",
        }
        for name, item in (("candidates.json", rule), ("thread-candidates.json", thread)):
            with open(os.path.join(cards, name), "w", encoding="utf-8") as handle:
                json.dump({"v": 1, "items": [item]}, handle)
        self.write(
            "handoffs/alpha.md",
            "# Handoffs · alpha\n\n## 2026-01-01 10:00 · - · earlier work · abcd1234\nRepos: alpha\n"
            "Tickets: -\nDid things.\nLearned: e2e-learned-sentence\n",
        )
        run = self.go("Reply with the single word: ok", cwd=self.work)
        text = "\n".join(self.contexts(run, "SessionStart", "candidates"))
        self.assertIn("Juno rule candidates (1 pending", text)
        self.assertIn("r0a1b2c", text)
        self.assertIn("juno rule accept", text)
        self.assertIn("Juno thread close candidates (1 pending", text)
        self.assertIn("PR alpha#7 merged", text)
        self.assertIn("e2e-learned-sentence", self.read_vault("_generated/learned.md"))


@e2e
class RuleCandidateApprovalLoop(CardE2E):
    QUOTE = "From now on always run the linter before committing in every repo of mine"
    RULE = "Run the linter before committing in every repo"

    def candidates(self) -> list[dict[str, Any]]:
        path = os.path.join(self.sb.state, "cards", "candidates.json")
        with open(path, encoding="utf-8") as handle:
            return list(json.load(handle)["items"])

    def wait_idle(self, seconds: float = 60.0) -> None:
        deadline = time.time() + seconds
        while self.jobs() and time.time() < deadline:
            time.sleep(0.5)

    def test_preference_to_candidate_to_nudge_to_accept_to_injection(self) -> None:
        self.extra_env.update(
            JUNO_CARD_CLAUDE=self.script("wrap-claude", RULE_WRAPPER),
            WRAP_CLAUDE=shutil.which("claude") or "claude",
            WRAP_RULE=self.RULE,
            WRAP_QUOTE=self.QUOTE,
        )
        repo = self.repo()
        self.go(
            f"Standing preference of mine for all future sessions: {self.QUOTE}. "
            f"{EDIT.format(repo=repo)}",
            cwd=repo,
        )
        self.wait_runs(1)
        (item,) = [i for i in self.candidates() if i["status"] == "pending"]
        cid = item["id"]
        self.assertRegex(cid, r"^r[0-9a-f]{6}$")
        self.assertNotIn("linter", self.read_vault("rules/global.md"))

        run = self.go("Reply with the single word: ok", cwd=self.work)
        nudge = "\n".join(self.contexts(run, "SessionStart", "candidates"))
        self.assertIn(cid, nudge)
        self.assertIn("juno rule accept", nudge)
        self.wait_idle()

        run = self.go(
            f"I choose {cid} to be accepted. " + command(harness.JUNO_BIN, "rule", "accept", cid),
            cwd=self.work,
            allow=["Bash"],
        )
        self.wait_idle()
        self.assertIn(f"Juno: rule candidate {cid} added to rules/global.md", run.raw)
        self.assertIn(self.RULE, self.read_vault("rules/global.md"))
        self.assertEqual({i["id"]: i["status"] for i in self.candidates()}[cid], "accepted")

        run = self.go("Reply with the single word: ok", cwd=self.work)
        injected = "\n".join(self.contexts(run, "SessionStart", "RULES"))
        self.assertIn(self.RULE, injected)
        self.wait_idle()


if __name__ == "__main__":
    unittest.main()
