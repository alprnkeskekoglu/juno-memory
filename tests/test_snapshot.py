import json
import os
import shutil
import subprocess
import tempfile
import unittest

from juno.adapters import git
from tests.support.write import WriteCase

GPG = shutil.which("gpg")
GPGCONF = shutil.which("gpgconf")
CLEAN = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}


class SnapshotCase(WriteCase):
    def gitconfig(self, text: str) -> None:
        with open(os.path.join(self.sb.home, ".gitconfig"), "w", encoding="utf-8") as handle:
            handle.write(text)

    def git(
        self, *args: str, extra: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = dict(CLEAN, HOME=self.sb.home, **(extra or {}))
        return subprocess.run(
            ["git", "-C", self.sb.vault, *args], capture_output=True, text=True, env=env, timeout=60
        )

    def head(self) -> str:
        done = self.git("rev-parse", "--verify", "-q", "HEAD")
        return done.stdout.strip() if done.returncode == 0 else ""

    def change(self, text: str = "a note\n") -> None:
        self.put("notes/n.md", text)


class Basics(SnapshotCase):
    def setUp(self) -> None:
        super().setUp()
        self.gitconfig("[user]\n\tname = T\n\temail = t@example.com\n[commit]\n\tgpgsign = false\n")

    def test_unsigned_config_commits_with_the_default_message(self) -> None:
        self.change()
        code, out = self.juno("snapshot")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip(), self.head())
        subject = self.git("log", "-1", "--format=%s").stdout.strip()
        self.assertRegex(subject, r"^juno snapshot \d{4}-\d\d-\d\d$")

    def test_message_json_and_nothing_to_commit(self) -> None:
        self.change()
        data = json.loads(self.juno("snapshot", "-m", "checkpoint", "--json")[1])
        self.assertEqual((data["status"], data["message"]), ("committed", "checkpoint"))
        self.assertEqual(self.juno("snapshot"), (0, "nothing to commit\n"))
        self.assertEqual(self.head(), data["commit"])

    def test_refused_in_a_subagent_and_without_a_ledger(self) -> None:
        self.change()
        self.assertEqual(self.juno("snapshot", env={"CLAUDE_CODE_AGENT_ID": "a"})[0], 2)
        self.assertEqual(self.juno("snapshot", env={"CLAUDE_CODE_SESSION_ID": ""})[0], 2)
        self.assertEqual(self.head(), "")

    def test_vault_without_its_own_git_fails_with_one_line(self) -> None:
        shutil.rmtree(os.path.join(self.sb.vault, ".git"))
        code, out = self.juno("snapshot")
        self.assertEqual(
            (code, out), (2, "Juno: snapshot failed: vault has no .git directory of its own\n")
        )

    def test_git_variables_from_the_caller_are_ignored(self) -> None:
        self.change()
        decoy = os.path.join(self.sb.root, "decoy")
        os.makedirs(decoy)
        code, out = self.juno(
            "snapshot",
            env={"GIT_DIR": decoy, "GIT_WORK_TREE": decoy, "GIT_INDEX_FILE": decoy + "/i"},
        )
        self.assertEqual(code, 0, out)
        self.assertTrue(self.head())

    def test_hooks_in_the_vault_do_not_run(self) -> None:
        hook = os.path.join(self.sb.vault, ".git", "hooks", "pre-commit")
        os.makedirs(os.path.dirname(hook), exist_ok=True)
        with open(hook, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nexit 1\n")
        os.chmod(hook, 0o755)
        self.change()
        self.assertEqual(self.juno("snapshot")[0], 0)


class SigningFailure(SnapshotCase):
    def test_a_broken_signer_commits_nothing_and_prints_one_line(self) -> None:
        self.gitconfig(
            "[user]\n\tname = T\n\temail = t@example.com\n\tsigningkey = DEADBEEF\n"
            "[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = /nonexistent/gpg\n"
        )
        self.change()
        code, out = self.juno("snapshot")
        self.assertEqual(code, 2)
        self.assertEqual(len(out.splitlines()), 1, out)
        self.assertRegex(out, r"^Juno: snapshot failed: commit signing failed \(.+\)\n$")
        self.assertEqual(self.head(), "")


@unittest.skipUnless(GPG, "gpg not installed")
class SignedCommit(SnapshotCase):
    def setUp(self) -> None:
        super().setUp()
        assert GPG
        self.gnupg = tempfile.mkdtemp(prefix="juno-gpg-", dir="/tmp")
        self.addCleanup(shutil.rmtree, self.gnupg, True)
        if GPGCONF:
            self.addCleanup(
                subprocess.run,
                [GPGCONF, "--homedir", self.gnupg, "--kill", "all"],
                capture_output=True,
            )
        os.chmod(self.gnupg, 0o700)
        made = subprocess.run(
            [
                GPG,
                "--homedir",
                self.gnupg,
                "--batch",
                "--passphrase",
                "",
                "--quick-gen-key",
                "Juno Test <t@example.com>",
                "default",
                "default",
                "never",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if made.returncode != 0:
            self.skipTest(f"cannot generate a temporary key: {made.stderr.strip()[:100]}")
        listing = subprocess.run(
            [GPG, "--homedir", self.gnupg, "--list-secret-keys", "--with-colons"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        fingerprint = next(x.split(":")[9] for x in listing.splitlines() if x.startswith("fpr:"))
        self.gitconfig(
            f"[user]\n\tname = T\n\temail = t@example.com\n\tsigningkey = {fingerprint}\n"
            f"[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = {GPG}\n"
        )
        self.env["GNUPGHOME"] = self.gnupg

    def test_snapshot_makes_a_signed_commit(self) -> None:
        self.change()
        code, out = self.juno("snapshot", "--json")
        self.assertEqual(code, 0, out)
        self.assertIn(json.loads(out)["signature"], ("G", "U"))
        shown = self.git("log", "-1", "--show-signature", extra={"GNUPGHOME": self.gnupg})
        self.assertIn("gpg: Good signature", shown.stdout + shown.stderr)


class RunsGitCleanly(unittest.TestCase):
    def test_result_strips_git_variables(self) -> None:
        done = git.result(["--version"], ".", 10, {"GIT_DIR": "/decoy", "PATH": CLEAN["PATH"]})
        self.assertIsNotNone(done)
        assert done is not None
        self.assertEqual(done.returncode, 0)


if __name__ == "__main__":
    unittest.main()
