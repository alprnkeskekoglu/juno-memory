import glob
import json
import os
import shutil
import subprocess
import sys
import unittest
import uuid

from tests.e2e import harness
from tests.e2e.harness import Run
from tests.support import REPO, Sandbox, e2e

MAESTRO = os.path.expanduser(os.environ.get("JUNO_E2E_MAESTRO", "~/maestro-checkout"))
MANTIS = os.path.expanduser(os.environ.get("JUNO_E2E_MANTIS", "~/mantis-checkout"))
RETRO = os.path.join(MAESTRO, "scripts", "retro.py")
PROBE = os.path.join(REPO, "tests", "e2e", "mantis_probe.py")


def command(*words: str) -> str:
    return (
        "Run exactly this command with the Bash tool, then show its output verbatim: "
        + " ".join(words)
    )


def need(case: unittest.TestCase, path: str) -> None:
    if not os.path.isdir(path):
        case.skipTest(f"{path} is not available")


class Case(unittest.TestCase):
    def setUp(self) -> None:
        need(self, MAESTRO)
        harness.require_claude(self)
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        done = harness.init_with_bin(self.sb)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.work = os.path.join(self.sb.root, "work")
        os.makedirs(self.work)
        self.maestro_work = os.path.join(self.sb.root, "maestro-work")
        self.repos = os.path.join(self.sb.root, "repos")
        self.alpha = os.path.join(self.repos, "alpha")
        os.makedirs(self.alpha)
        os.makedirs(os.path.join(self.alpha, ".git"))
        with open(os.path.join(self.alpha, "README.md"), "w", encoding="utf-8") as handle:
            handle.write("alpha\n")

    def env(self, interactive: bool = True) -> dict[str, str]:
        env = harness.claude_env(self.sb, interactive)
        env["PATH"] = self.sb.bin + os.pathsep + env.get("PATH", "")
        env["JUNO_CLAUDE_PROJECTS"] = os.path.join(self.sb.root, "claude-projects")
        env["JUNO_REPO_ROOTS"] = self.repos
        return env

    def vp(self, rel: str) -> str:
        return os.path.join(self.sb.vault, *rel.split("/"))

    def put(self, rel: str, text: str) -> None:
        os.makedirs(os.path.dirname(self.vp(rel)), exist_ok=True)
        with open(self.vp(rel), "w", encoding="utf-8") as handle:
            handle.write(text)

    def get(self, rel: str) -> str:
        try:
            with open(self.vp(rel), encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return ""

    def tool_outputs(self, run: Run) -> str:
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
        return "\n".join(found)

    def touch_alpha(self) -> str:
        return f"First read {self.alpha}/README.md with the Read tool (the synth verified the code fact there). "

    def go(self, prompt: str, interactive: bool = True, allow: list[str] | None = None) -> Run:
        extra = [
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            *(allow or ["Bash", "Write", "Read"]),
            "--plugin-dir",
            MAESTRO,
        ]
        run = harness.run_claude(
            prompt, self.env(interactive), cwd=self.work, extra=extra, timeout=360
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(harness.juno_hook_failures(run), [])
        for response in run.hook_responses:
            self.assertIn(response.get("exit_code"), (0, None), response)
        return run

    def writes(self) -> list[dict[str, object]]:
        path = os.path.join(self.sb.state, "writes.jsonl")
        try:
            with open(path, encoding="utf-8") as handle:
                return [json.loads(line) for line in handle.read().splitlines()]
        except FileNotFoundError:
            return []

    def retro_script(self, *words: str) -> str:
        return " ".join(
            [
                "python3",
                RETRO,
                "--work-root",
                self.maestro_work,
                "--vault",
                self.sb.vault,
                *words,
            ]
        )

    def state(self, run_id: str, state: dict[str, object]) -> str:
        name = run_id[4:] if run_id.startswith("SES-") else run_id
        wd = os.path.join(self.maestro_work, *(["_session"] if name != run_id else []), name)
        os.makedirs(os.path.join(wd, "retro"))
        with open(os.path.join(wd, "state.json"), "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        return wd


@e2e
class MaestroRetroRepoEvidence(Case):
    def test_repo_evidence_is_refused_when_the_session_never_touched_the_repo(self) -> None:
        run = self.go(
            command("juno project gotchas --repo alpha --evidence", '"repo:alpha"', '"A gotcha"')
        )
        self.assertIn("is not anchored in this session", self.tool_outputs(run), run.raw[-2500:])
        self.assertEqual(self.get("projects/alpha.md"), "")


@e2e
class MaestroRetroRepoGotcha(Case):
    def test_gotcha_verb_creates_the_profile_and_logs_the_write(self) -> None:
        token = "gotcha" + uuid.uuid4().hex[:6]
        text = f"Exceptions render in bootstrap/app.php withExceptions ({token})"
        run = self.go(
            self.touch_alpha()
            + "You are running step R6 of the maestro retro skill for the repo alpha. "
            + command(
                "juno project gotchas --repo alpha --evidence",
                '"repo:alpha"',
                f'"{text}"',
            )
        )
        profile = self.get("projects/alpha.md")
        self.assertRegex(profile, rf"(?s)## Gotchas.*{token}", run.raw[-2500:])
        self.assertTrue(profile.startswith("---\nrepo: alpha\n"), profile)
        log = [w for w in self.writes() if w.get("file") == "projects/alpha.md"]
        self.assertTrue(log, self.writes())
        self.assertEqual({str(w.get("verb")) for w in log}, {"project"})


@e2e
class MaestroRetroRuntimeBlock(Case):
    def test_block_from_runtime_diff_lands_in_the_profile_frontmatter(self) -> None:
        self.put("projects/alpha.md", "---\nrepo: alpha\nupdated: 2026-10-01\n---\n\n## Gotchas\n")
        wd = self.state(
            "ALPHA-1",
            {
                "key": "ALPHA-1",
                "stage": "done",
                "repos": [{"name": "alpha", "path": "~/projects/alpha"}],
            },
        )
        candidate = {
            "id": "C1",
            "class": "runtime",
            "dest": "projects/alpha.md#runtime",
            "text": "alpha runs in the alpha-php container",
            "fields": {"container": "alpha-php", "lang": "php", "root": "/var/alpha"},
            "evidence": [{"kind": "user", "ref": "11111111:3", "quote": "q"}],
        }
        with open(os.path.join(wd, "retro", "candidates.json"), "w", encoding="utf-8") as handle:
            json.dump({"candidates": [candidate]}, handle)
        block_file = "/tmp/juno-runtime-alpha.txt"
        self.addCleanup(lambda: os.path.exists(block_file) and os.remove(block_file))
        expected = "runtime:\n  container: alpha-php\n  root: /var/alpha\n  lang: php\n"
        run = self.go(
            self.touch_alpha()
            + "You are running step R6 of the maestro retro skill, runtime fact item. "
            "Step 1: run exactly this with the Bash tool and read its JSON output: "
            + self.retro_script("runtime-diff", "ALPHA-1", "C1")
            + f". Step 2: write the `block` field of that JSON, verbatim and followed by one newline, "
            f"with the Write tool to {block_file}. Step 3: "
            + command(
                "juno project runtime --repo alpha --evidence",
                '"repo:alpha"',
                f"--block-file {block_file}",
            )
        )
        profile = self.get("projects/alpha.md")
        self.assertIn(expected + "---\n", profile, run.raw[-2500:])
        self.assertTrue(profile.startswith("---\nrepo: alpha\nupdated: "))
        self.assertIn("## Gotchas", profile)
        self.assertIn(
            "runtime block written to projects/alpha.md", self.tool_outputs(run), run.raw[-2500:]
        )
        self.assertIn(
            ("project", "projects/alpha.md"),
            {(str(w.get("verb")), str(w.get("file"))) for w in self.writes()},
        )


@e2e
class MaestroRetroBacklogRoundTrip(Case):
    def test_backlog_add_then_existing_reads_it_back_as_json(self) -> None:
        label = "[maestro] Retro should read the juno backlog"
        title = "Retro should read the juno backlog"
        sid = str(uuid.uuid4())
        run_id = "SES-" + sid
        wd = self.state(
            run_id,
            {"skill": "session", "session_id": sid, "stage": "done", "repos": []},
        )
        candidate = {
            "id": "C1",
            "class": "pipeline",
            "dest": "juno:backlog",
            "text": f"{title}\nEvidence: the session offered it\n"
            "Fix: parse juno backlog list --json in retro_session_existing.py",
            "evidence": [],
        }
        with open(os.path.join(wd, "retro", "candidates.json"), "w", encoding="utf-8") as handle:
            json.dump({"candidates": [candidate]}, handle)
        body_file = "/tmp/juno-backlog-C1.txt"
        self.addCleanup(lambda: os.path.exists(body_file) and os.remove(body_file))
        projects = os.path.join(self.sb.root, "claude-projects")
        os.makedirs(projects, exist_ok=True)
        run = self.go(
            f'The user chose this option label: "{label}". You are running the maestro retro '
            "skill R6 Juno backlog item. Step 1: run exactly this with the Bash tool and read its "
            "JSON: "
            + self.retro_script("backlog-match", run_id, "C1")
            + f". Step 2: write its `body` field, with real line breaks, with the Write tool to "
            f"{body_file}. Step 3: "
            + command(
                "juno backlog add --quote",
                f'"{label}"',
                f'--title "{title}" --priority p3 --body-file {body_file}',
            )
            + " Step 4: "
            + command(self.retro_script("--projects-root", projects, "existing", run_id))
        )
        listing = subprocess.run(
            [os.path.join(self.sb.bin, "juno"), "backlog", "list", "--json"],
            capture_output=True,
            text=True,
            env=self.sb.env,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        items = json.loads(listing.stdout)["items"]
        self.assertEqual([i["title"] for i in items], [title], run.raw[-2500:])
        existing = os.path.join(wd, "retro", "existing.md")
        self.assertTrue(os.path.exists(existing), run.raw[-2500:])
        with open(existing, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("## Backlog open, juno (1)", text)
        self.assertIn(title, text)
        self.assertTrue(glob.glob(os.path.join(self.sb.vault, "backlog", "*.md")))


@e2e
class MantisReadsTheV2Vault(Case):
    def setUp(self) -> None:
        need(self, MANTIS)
        if shutil.which("uv") is None:
            self.skipTest("uv not installed")
        super().setUp()

    def test_mantis_hands_the_v2_vault_to_investigate_and_reads_no_v1_path(self) -> None:
        self.put(
            "projects/alpha.md", "---\nrepo: alpha\nupdated: 2026-11-01\n---\n\n## Gotchas\n- a\n"
        )
        self.put("rules/repos/alpha.md", "- Run the formatter (src: user 2026-11-01)\n")
        self.put(
            "handoffs/2026-11.md",
            "## 2026-11-01 09:00 · - · work · abcd1234\nRepos: alpha\nDid it.\nLearned: none.\n",
        )
        home = os.path.join(self.sb.root, "mantis-home")
        shutil.copytree(os.path.join(MANTIS, "config"), os.path.join(home, "config"))
        path = os.path.join(home, "config", "mantis.json")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        data["paths"] = {"saga": self.sb.vault, "state": os.path.join(home, "state")}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        user = os.path.join(self.sb.root, "mantis-user")
        os.makedirs(user)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("JUNO_", "VIRTUAL_ENV"))}
        env.update(HOME=user, MANTIS_HOME=home)
        done = subprocess.run(
            ["uv", "run", "--project", MANTIS, "python", PROBE, home, self.work],
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=300,
            cwd=MANTIS,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        seen = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(seen["saga"], self.sb.vault)
        self.assertIn(self.sb.vault, seen["add_dirs"])
        self.assertIn(self.sb.vault, seen["protected"])
        root = seen["saga"]
        self.assertTrue(os.path.isfile(os.path.join(root, "projects", "alpha.md")))
        self.assertTrue(os.path.isfile(os.path.join(root, "rules", "repos", "alpha.md")))
        self.assertTrue(glob.glob(os.path.join(root, "handoffs", "*.md")))
        reader = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]);"
                "import runtime_registry, retro_session_existing as e;"
                "print(len(e.session_cards(sys.argv[2], 'abcd1234')),"
                "len(e.rule_lines(sys.argv[2])))",
                os.path.join(MAESTRO, "scripts"),
                root,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(reader.stdout.split()[0], "1", reader.stdout + reader.stderr)
