import json
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from typing import Any

from juno.adapters import queues
from tests.e2e import harness
from tests.e2e.harness import JUNO_BIN, Run
from tests.support import Sandbox, e2e

QUOTE = "always run the formatter before every commit"
RULE = "Run the formatter before every commit"
BODY = (
    "## Task\nClean up the invoice export job\n## Why\nIt times out on large partners\n"
    "## Research\nLook at the batch size\n## First step\nProfile one export run\n"
)
CARD = "## {day} 09:00 · - · work · {seg}\nRepos: app\nDid something.\nLearned: Lesson {n}.\n\n"


class Case(unittest.TestCase):
    def setUp(self) -> None:
        harness.require_claude(self)
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        done = harness.init_with_bin(self.sb)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.work = os.path.join(self.sb.root, "work")
        os.makedirs(self.work)

    def env(self, interactive: bool = True, **extra: str) -> dict[str, str]:
        env = harness.claude_env(self.sb, interactive)
        env["JUNO_CLAUDE_PROJECTS"] = os.path.join(self.sb.root, "claude-projects")
        env.update(extra)
        return env

    def go(
        self, prompt: str, interactive: bool = True, allow: list[str] | None = None, **extra: str
    ) -> Run:
        args = ["--permission-mode", "acceptEdits", "--allowedTools", *(allow or ["Bash"])]
        run = harness.run_claude(prompt, self.env(interactive, **extra), cwd=self.work, extra=args)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(harness.juno_hook_failures(run), [])
        for response in run.hook_responses:
            self.assertIn(response.get("exit_code"), (0, None), response)
            text = harness.context_of(response)
            if text is not None:
                self.assertLessEqual(len(text), harness.HOOK_CAP)
        return run

    def vp(self, rel: str) -> str:
        return os.path.join(self.sb.vault, *rel.split("/"))

    def put(self, rel: str, text: str) -> str:
        os.makedirs(os.path.dirname(self.vp(rel)), exist_ok=True)
        with open(self.vp(rel), "w", encoding="utf-8") as handle:
            handle.write(text)
        return self.vp(rel)

    def get(self, rel: str) -> str:
        try:
            with open(self.vp(rel), encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return ""

    def body_file(self, name: str = "body.md") -> str:
        os.makedirs(self.sb.state, exist_ok=True)
        path = os.path.join(self.sb.state, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(BODY)
        return path

    def seed(self, queue: queues.Queue, items: list[dict[str, Any]]) -> None:
        from juno import config

        queues.write(config.load(dict(self.sb.env)), queue, items)

    def status_of(self, queue: queues.Queue) -> dict[str, str]:
        from juno import config

        cfg = config.load(dict(self.sb.env))
        return {str(i["id"]): str(i.get("status")) for i in queues.read(cfg, queue)}

    def ledger_events(self) -> list[dict[str, Any]]:
        names = os.listdir(os.path.join(self.sb.state, "ledgers"))
        path = os.path.join(self.sb.state, "ledgers", sorted(names)[0])
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines()]

    def bash_outputs(self, run: Run) -> list[str]:
        found: list[str] = []
        for event in run.events:
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if event.get("type") != "user" or not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    body = block.get("content")
                    if isinstance(body, list):
                        body = "\n".join(
                            str(b.get("text", "")) for b in body if isinstance(b, dict)
                        )
                    found.append(str(body))
        return found

    def output(self, run: Run) -> str:
        return "\n".join(self.bash_outputs(run))

    def doctor(self) -> tuple[int, list[dict[str, str]]]:
        done = harness.shim(self.sb, "doctor", "--json", *self.sb.install_args())
        return done.returncode, json.loads(done.stdout)["checks"]


def command(*words: str) -> str:
    return (
        "Run exactly this command with the Bash tool, then show its output verbatim: "
        + " ".join(words)
    )


@e2e
class RuleAddQuoteVerifiedAndInjected(Case):
    def test_typed_quote_succeeds_and_the_rule_arrives_in_the_next_session(self) -> None:
        token = "fmt" + uuid.uuid4().hex[:6]
        rule = f"{RULE} ({token})"
        run = self.go(
            f"Note for the record: {QUOTE}. "
            + command(
                JUNO_BIN, "rule", "add", "--scope", "global", "--quote", f'"{QUOTE}"', f'"{rule}"'
            )
        )
        out = self.output(run)
        self.assertIn("Juno: rule added to rules/global.md", out, run.raw[-2500:])
        text = self.get("rules/global.md")
        self.assertRegex(text, rf'\({token}\) · src: user \S+ · "{QUOTE}"')
        added = [e for e in self.ledger_events() if e["ev"] == "rule_added"]
        self.assertEqual(added[0]["scope"], "global")
        with open(os.path.join(self.sb.state, "status.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["last_rule"]["scope"], "global")
        second = self.go("Reply with the single word: ok")
        contexts = harness.juno_contexts(second, "SessionStart")
        self.assertTrue(contexts, second.raw[-1500:])
        self.assertIn(rule, contexts[0])
        self.assertNotIn("src: user", contexts[0])


@e2e
class RuleAddFabricatedQuoteRefused(Case):
    def test_a_quote_the_user_never_typed_is_refused(self) -> None:
        run = self.go(
            "Build the quote text by joining these two pieces with one space: piece one is "
            "'we agreed that releases' and piece two is 'always happen on fridays'. "
            + command(
                JUNO_BIN,
                "rule",
                "add",
                "--scope",
                "global",
                "--quote",
                "<the joined quote in double quotes>",
                '"Releases always happen on fridays"',
            )
        )
        out = self.output(run)
        self.assertIn("Juno: refused: quote is not a verbatim part", out, run.raw[-2500:])
        self.assertNotIn("fridays", self.get("rules/global.md"))


@e2e
class WriteVerbRefusedWithoutOverride(Case):
    def test_headless_session_cannot_write(self) -> None:
        run = self.go(
            command(
                JUNO_BIN, "rule", "add", "--scope", "global", "--quote", f'"{QUOTE}"', f'"{RULE}"'
            ),
            interactive=False,
        )
        self.assertIn(
            "Juno: refused: not an interactive session", self.output(run), run.raw[-2000:]
        )
        self.assertEqual(self.get("rules/global.md").count("\n- "), 0)
        self.assertFalse(os.path.isdir(os.path.join(self.sb.state, "ledgers")))


@e2e
class SubagentWriteRefused(Case):
    def test_a_subagent_running_a_write_verb_is_refused(self) -> None:
        run = self.go(
            "Use the Agent tool exactly once with subagent_type general-purpose and this prompt: "
            f"'Run this command with the Bash tool and reply with its full output: {JUNO_BIN} rule add "
            f'--scope global --quote "{QUOTE}" "{RULE}"\'. Then reply with exactly what the '
            f"subagent answered. {QUOTE}.",
            allow=["Agent", "Task", "Bash"],
        )
        self.assertIn(
            "Juno: refused: agent context", run.result + self.output(run), run.raw[-3000:]
        )
        self.assertEqual(self.get("rules/global.md").count("\n- "), 0)


@e2e
class BacklogAddAndList(Case):
    def test_add_then_list(self) -> None:
        quote = "put the invoice export cleanup on my work list for next week"
        self.body_file()
        run = self.go(
            f"Please {quote}. "
            + command(
                JUNO_BIN,
                "backlog",
                "add",
                "--quote",
                f'"{quote}"',
                "--title",
                '"Invoice export cleanup"',
                "--priority",
                "p2",
                "--body-file",
                self.body_file(),
            )
            + f" Then run: {JUNO_BIN} backlog list"
        )
        out = self.output(run)
        self.assertIn(
            "Juno: backlog item added: backlog/invoice-export-cleanup.md", out, run.raw[-2500:]
        )
        self.assertIn("invoice-export-cleanup · inbox · p2", out)
        self.assertIn("src: user", self.get("backlog/invoice-export-cleanup.md"))
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "body.md")))


@e2e
class ThreadAddAndClose(Case):
    def test_add_then_close_a_candidate_by_id(self) -> None:
        run = self.go(
            command(
                JUNO_BIN,
                "thread",
                "add",
                "--tag",
                "app",
                '"Decide on the retry policy for the sync job"',
            )
        )
        self.assertIn("Juno: thread opened in threads.md", self.output(run), run.raw[-2000:])
        line = [x for x in self.get("threads.md").splitlines() if x.startswith("- [app]")][0]
        self.seed(
            queues.THREADS,
            [
                {
                    "id": "t5a5a5a",
                    "line": line,
                    "signals": ["open for 40 days"],
                    "t": 1.0,
                    "status": "pending",
                }
            ],
        )
        run = self.go(
            "I choose t5a5a5a to be closed. " + command(JUNO_BIN, "thread", "close", "t5a5a5a")
        )
        self.assertIn("Juno: thread t5a5a5a closed", self.output(run), run.raw[-2000:])
        self.assertNotIn("retry policy", self.get("threads.md"))
        self.assertEqual(self.status_of(queues.THREADS), {"t5a5a5a": "closed"})


@e2e
class CompactAndDigest(Case):
    def test_compact_archives_old_cards_and_digest_lists_learned(self) -> None:
        text = "# Handoffs · app\n\n" + "".join(
            CARD.format(day=f"2026-09-{n + 1:02d}", seg=f"{n:08x}", n=n) for n in range(24)
        )
        self.put("handoffs/app.md", text)
        run = self.go(command(JUNO_BIN, "compact") + f" Then run: {JUNO_BIN} digest")
        out = self.output(run)
        self.assertIn("moved handoffs/app.md: 4", out, run.raw[-2500:])
        self.assertIn("rebuilt with 20 entries", out)
        self.assertEqual(self.get("handoffs/app.md").count("\n## 2026"), 20)
        self.assertIn("visibility: private", self.get("archive/handoffs/2026-09.md"))
        self.assertIn("Lesson 23.", self.get("_generated/learned.md"))


def real_git_signing() -> bool:
    done = subprocess.run(
        ["git", "config", "--global", "--get", "commit.gpgsign"], capture_output=True, text=True
    )
    return done.stdout.strip() == "true"


@e2e
class SnapshotSignedCommit(Case):
    @unittest.skipUnless(real_git_signing(), "the real git config does not sign commits")
    def test_snapshot_commits_the_temp_vault_with_a_signature(self) -> None:
        self.put("notes/e2e.md", "a note\n")
        run = self.go(command(JUNO_BIN, "snapshot", "--json"))
        out = self.output(run)
        self.assertIn('"status": "committed"', out, run.raw[-2500:])
        shown = subprocess.run(
            ["git", "-C", self.sb.vault, "log", "-1", "--show-signature"],
            capture_output=True,
            text=True,
            timeout=60,
            env=dict(os.environ, LC_ALL="C", LANGUAGE="C"),
        )
        self.assertIn("Good signature", shown.stdout + shown.stderr)

    def test_a_failing_signer_prints_one_line_and_commits_nothing(self) -> None:
        empty = tempfile.mkdtemp(prefix="juno-gnupg-", dir="/tmp")
        self.addCleanup(shutil.rmtree, empty, True)
        os.chmod(empty, 0o700)
        self.put("notes/e2e.md", "a note\n")
        run = self.go(command(JUNO_BIN, "snapshot"), GNUPGHOME=empty)
        out = self.output(run)
        if "committed" in out or "nothing to commit" in out:
            self.skipTest("the real git config does not sign commits")
        self.assertRegex(out, r"Juno: snapshot failed: commit signing failed \(", run.raw[-2500:])
        head = subprocess.run(
            ["git", "-C", self.sb.vault, "rev-parse", "--verify", "-q", "HEAD"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(head.returncode, 0)


@e2e
class DoctorCleanOnFreshInit(Case):
    def test_no_failures_after_init(self) -> None:
        code, checks = self.doctor()
        failures = [c for c in checks if c["status"] == "FAIL"]
        self.assertEqual((code, failures), (0, []))
        warns = {c["name"] for c in checks if c["status"] == "WARN"}
        self.assertLessEqual(warns, {"plugin", "shim"}, checks)


@e2e
class SkillsListed(Case):
    def test_juno_and_backlog_skills_are_in_the_init_event(self) -> None:
        run = self.go("Reply with the single word: ok")
        init = [e for e in run.events if e.get("type") == "system" and e.get("subtype") == "init"]
        skills = [str(s) for s in init[0].get("skills", [])]
        self.assertTrue(any(s in ("juno", "juno:juno") for s in skills), skills)
        self.assertTrue(any(s in ("backlog", "juno:backlog") for s in skills), skills)


if __name__ == "__main__":
    unittest.main()
