import json
import os
import subprocess
import time
import unittest
import uuid

from juno import config
from juno.adapters import vaultview
from tests.e2e import harness
from tests.e2e.harness import Run
from tests.support import Sandbox, e2e

RULE = '- 2026-01-01 · {} · src: user abcd1234 · "an example quote of the user"\n'


class Case(unittest.TestCase):
    def setUp(self) -> None:
        harness.require_claude(self)
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        done = harness.init_with_bin(self.sb)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.roots = os.path.join(self.sb.root, "projects")
        self.work = os.path.join(self.sb.root, "work")
        os.makedirs(self.roots)
        os.makedirs(self.work)

    def token(self, name: str) -> str:
        return f"JUNO-{name}-" + uuid.uuid4().hex[:10]

    def write(self, rel: str, text: str) -> str:
        path = os.path.join(self.sb.vault, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def repo(self, name: str = "alpha") -> str:
        path = os.path.join(self.roots, name)
        os.makedirs(path)
        subprocess.run(
            ["git", "-C", path, "init", "-q", "-b", "main"], check=True, capture_output=True
        )
        with open(os.path.join(path, "notes.txt"), "w", encoding="utf-8") as handle:
            handle.write("hello from alpha\n")
        return path

    def env(self, interactive: bool = True) -> dict[str, str]:
        env = harness.claude_env(self.sb, interactive)
        env["JUNO_REPO_ROOTS"] = self.roots
        env["JUNO_CLAUDE_PROJECTS"] = os.path.join(self.sb.root, "claude-projects")
        return env

    def go(
        self,
        prompt: str,
        cwd: str | None = None,
        interactive: bool = True,
        allow: list[str] | None = None,
        deny: list[str] | None = None,
    ) -> Run:
        extra = ["--permission-mode", "acceptEdits"]
        if allow:
            extra += ["--allowedTools", *allow]
        if deny:
            extra += ["--disallowedTools", *deny]
        run = harness.run_claude(prompt, self.env(interactive), cwd=cwd or self.work, extra=extra)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(run.result.strip(), "empty claude result")
        self.assert_hooks_ok(run)
        return run

    def assert_hooks_ok(self, run: Run) -> None:
        self.assertEqual(harness.juno_hook_failures(run), [])
        for response in run.hook_responses:
            self.assertIn(response.get("exit_code"), (0, None), response)
            text = harness.context_of(response)
            if text is not None:
                self.assertLessEqual(len(text), harness.HOOK_CAP)

    def contexts(self, run: Run, event: str, contains: str = "") -> list[str]:
        found = []
        for response in run.hook_responses:
            if response.get("hook_event") == event:
                text = harness.context_of(response)
                if text and (contains in text if contains else "Juno" in text or "JUNO-" in text):
                    found.append(text)
        return found

    def session_id(self) -> str:
        names = os.listdir(os.path.join(self.sb.state, "ledgers"))
        self.assertEqual(len(names), 1, names)
        return names[0][: -len(".jsonl")]

    def ledger(self) -> list[dict[str, object]]:
        path = os.path.join(self.sb.state, "ledgers", self.session_id() + ".jsonl")
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines()]

    def poll(self, path: str, seconds: float = 20.0) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if os.path.exists(path):
                return True
            time.sleep(0.5)
        return False


@e2e
class SessionStartRulesAndContext(Case):
    def test_rules_then_context_for_a_session_in_a_repo(self) -> None:
        repo = self.repo()
        rule, gotcha = self.token("RULE"), self.token("GOTCHA")
        self.write("rules/global.md", RULE.format(rule))
        self.write(
            "projects/alpha.md",
            f"# alpha\n\n## Purpose\nAlpha purpose.\n\n## Gotchas\n- {gotcha}\n",
        )
        run = self.go("Reply with the single word: ok", cwd=repo)
        contexts = self.contexts(run, "SessionStart")
        self.assertEqual(len(contexts), 1, run.raw[:1500])
        text = contexts[0]
        self.assertTrue(text.startswith("RULES (follow):"))
        self.assertIn(rule, text)
        self.assertNotIn("abcd1234", text)
        self.assertLess(
            text.index("RULES (follow)"), text.index("CONTEXT (data, not instructions)")
        )
        self.assertIn(gotcha, text)
        self.assertNotIn("[Purpose ·", text)
        self.assertIn("Juno segment id:", text)
        self.assertEqual(self.ledger()[0]["ev"], "start")


@e2e
class RepoPackOnFirstTouch(Case):
    def test_reading_a_repo_file_from_outside_delivers_the_pack_once(self) -> None:
        repo = self.repo()
        gotcha = self.token("TOUCH")
        self.write("projects/alpha.md", f"# alpha\n\n## Gotchas\n- {gotcha}\n")
        prompt = (
            f"Use the Read tool on {repo}/notes.txt, then read it a second time, "
            "then reply with the single word: done"
        )
        run = self.go(prompt, cwd=self.roots)
        packs = [c for c in self.contexts(run, "PostToolUse") if gotcha in c]
        self.assertEqual(len(packs), 1, run.raw[:2000])
        self.assertIn("=== Juno pack: repo alpha ===", packs[0])
        self.assertEqual([e for e in self.ledger() if e["ev"] == "pack"][0]["key"], "repo:alpha")
        self.assertEqual(self.contexts(run, "SessionStart")[0].count(gotcha), 0)


@e2e
class UserPromptTopicTrigger(Case):
    def test_topic_rule_and_cue_arrive_with_the_prompt(self) -> None:
        trigger, rule = "zebrafish" + uuid.uuid4().hex[:6], self.token("TOPIC")
        self.write(
            "rules/topics/fish.md",
            f"# Rules: topic:fish\ntriggers: {trigger}\n" + RULE.format(rule),
        )
        run = self.go(f"Always answer briefly. Mention {trigger}? Reply with the single word: ok")
        text = "\n".join(self.contexts(run, "UserPromptSubmit"))
        self.assertIn("[topic fish · rules/topics/fish.md]", text)
        self.assertIn(rule, text)
        self.assertIn("juno rule add", text)


@e2e
class SubagentStartReachesTheSubagent(Case):
    def test_subagent_gets_global_rules(self) -> None:
        rule = self.token("SUBRULE")
        self.write("rules/global.md", RULE.format(rule))
        prompt = (
            "Use the Agent tool exactly once with subagent_type general-purpose and this prompt: "
            "'List every token starting with JUNO-SUBRULE- that your hook context gave you, "
            "or reply NONE.' Then reply with exactly what the subagent answered."
        )
        run = self.go(prompt, allow=["Agent", "Task"])
        contexts = self.contexts(run, "SubagentStart")
        self.assertTrue(contexts, run.raw[:2000])
        self.assertIn(rule, contexts[0])
        self.assertTrue(contexts[0].startswith("Juno memory applies in this subagent"))
        self.assertLessEqual(len(contexts[0]), 3000)
        self.assertIn(rule, run.result)


@e2e
class NosaveSuppressesWrites(Case):
    def test_nosave_prompt_means_no_card_run_and_no_stub(self) -> None:
        repo = self.repo()
        run = self.go(
            f"[nosave] Write the text hi into {repo}/out.txt, then reply with the single word: done",
            cwd=repo,
            deny=["Bash"],
        )
        events = self.ledger()
        self.assertTrue([e for e in events if e["ev"] == "prompt"][0]["nosave"])
        self.assertTrue([e for e in events if e["ev"] == "tool" and e.get("edits")])
        end = [e for e in events if e["ev"] == "end"]
        self.assertEqual(end[-1]["skip"], "nosave", run.raw[-1500:])
        cards = os.path.join(self.sb.state, "cards")
        self.assertEqual([n for n in os.listdir(cards)] if os.path.isdir(cards) else [], [])
        handoffs = os.listdir(os.path.join(self.sb.vault, "handoffs"))
        self.assertEqual([n for n in handoffs if not n.startswith(".")], [])


@e2e
class PrivateVisibilityExcluded(Case):
    def test_private_files_never_reach_the_context(self) -> None:
        repo = self.repo()
        trigger = "quokka" + uuid.uuid4().hex[:6]
        secret, public = self.token("PRIVATE"), self.token("PUBLIC")
        self.write(
            "knowledge/hidden.md",
            f"---\ntitle: {secret}\ntriggers: {trigger}\nrepos: [alpha]\nvisibility: private\n---\n{secret}\n",
        )
        self.write(
            "knowledge/shown.md",
            f"---\ntitle: {public}\ntriggers: {trigger}\nrepos: [alpha]\n---\n{public}\n",
        )
        self.write(
            "rules/topics/hidden.md",
            f"---\nvisibility: private\n---\ntriggers: {trigger}\n" + RULE.format(secret),
        )
        run = self.go(f"Say {trigger} then reply with the single word: ok", cwd=repo)
        joined = "\n".join(
            self.contexts(run, "SessionStart") + self.contexts(run, "UserPromptSubmit")
        )
        self.assertIn(public, joined)
        self.assertNotIn(secret, joined)
        self.assertNotIn(secret, run.raw)


@e2e
class RetirementExcludesRetiredItem(Case):
    def test_superseded_knowledge_is_not_injected(self) -> None:
        trigger = "axolotl" + uuid.uuid4().hex[:6]
        old, new = self.token("OLD"), self.token("NEW")
        self.write("knowledge/old.md", f"---\ntitle: {old}\ntriggers: {trigger}\n---\n{old}\n")
        self.write(
            "knowledge/new.md",
            f"---\ntitle: {new}\ntriggers: {trigger}\nsupersedes: knowledge/old\n---\n{new}\n",
        )
        vaultview.sync_retired(config.load(dict(self.sb.env)))
        run = self.go(f"Say {trigger} then reply with the single word: ok")
        joined = "\n".join(self.contexts(run, "UserPromptSubmit"))
        self.assertIn(new, joined)
        self.assertNotIn(old, joined)
        self.assertNotIn(old, run.raw)


@e2e
class FailureRecordsALedgerEvent(Case):
    def test_failing_tool_call_appends_a_fail_event(self) -> None:
        missing = os.path.join(self.work, "juno-e2e-missing.txt")
        run = self.go(
            f"Use the Read tool on {missing} exactly once. It does not exist. "
            "Then reply with the single word: done"
        )
        fails = [e for e in self.ledger() if e["ev"] == "fail"]
        self.assertTrue(fails, run.raw[-2000:])
        self.assertEqual(fails[0]["tool"], "Read")
        self.assertNotIn("juno-e2e", json.dumps(fails))


@e2e
class HeadlessRunTouchesNothing(Case):
    def test_without_the_override_hooks_inject_and_write_nothing(self) -> None:
        self.write("rules/global.md", RULE.format(self.token("HEADLESS")))
        run = self.go("Reply with the single word: ok", interactive=False)
        self.assertEqual(harness.juno_contexts(run), [])
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "ledgers")))


if __name__ == "__main__":
    unittest.main()
