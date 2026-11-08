import os
import subprocess
import unittest
import uuid

from tests.e2e import harness
from tests.support import Sandbox, e2e, settings_variants
from tests.test_init import tree

PROMPT = (
    "Look at the RULES text injected at session start. If it contains a unique token that starts "
    "with JUNO-E2E-, reply with exactly that token and nothing else; otherwise reply exactly: NONE"
)


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)

    def seed_token(self) -> str:
        token = "JUNO-E2E-" + uuid.uuid4().hex[:12]
        with open(os.path.join(self.sb.vault, "core.md"), "a", encoding="utf-8") as handle:
            handle.write(f"\nThe unique session token is {token}.\n")
        return token

    def init(self) -> None:
        done = harness.init_with_bin(self.sb)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


@e2e
class SessionStartInjection(Case):
    def test_p_session_with_override_sees_core_md(self) -> None:
        harness.require_claude(self)
        self.init()
        token = self.seed_token()
        run = harness.run_claude(PROMPT, harness.claude_env(self.sb, interactive=True))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(run.result.strip(), "empty claude result")
        contexts = harness.juno_contexts(run, "SessionStart")
        self.assertEqual(len(contexts), 1, run.raw[:2000])
        self.assertIn(token, contexts[0])
        self.assertTrue(contexts[0].startswith("RULES (follow):"))
        self.assertLessEqual(len(contexts[0]), harness.HOOK_CAP)
        self.assertEqual(harness.juno_hook_failures(run), [])
        self.assertIn(token, run.result)
        self.assertTrue(os.path.exists(os.path.join(self.sb.state, "hook-timings.log")))


@e2e
class HeadlessInjectsNothing(Case):
    def test_p_session_without_override_gets_no_injection(self) -> None:
        harness.require_claude(self)
        self.init()
        token = self.seed_token()
        run = harness.run_claude(PROMPT, harness.claude_env(self.sb, interactive=False))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(run.result.strip(), "empty claude result")
        self.assertEqual(harness.juno_contexts(run), [])
        self.assertNotIn(token, run.raw)
        self.assertEqual(harness.juno_hook_failures(run), [])
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "hook-timings.log")))

    def test_override_with_default_config_path_is_ignored(self) -> None:
        harness.require_claude(self)
        self.init()
        token = self.seed_token()
        env = harness.claude_env(self.sb, interactive=True)
        xdg = os.path.join(self.sb.root, "xdg")
        default_config = os.path.join(xdg, "juno", "juno.toml")
        os.makedirs(os.path.dirname(default_config))
        with open(self.sb.config, "rb") as src, open(default_config, "wb") as dst:
            dst.write(src.read())
        env["XDG_CONFIG_HOME"] = xdg
        env["JUNO_CONFIG"] = default_config
        run = harness.run_claude(PROMPT, env)
        self.assertEqual(harness.juno_contexts(run), [])
        self.assertNotIn(token, run.raw)


@e2e
class UninstallRestoresSettings(Case):
    def test_byte_identical_for_every_settings_shape(self) -> None:
        for name, raw in settings_variants().items():
            with self.subTest(name):
                self.sb.cleanup()
                self.sb = Sandbox()
                self.sb.write_settings(raw)
                self.init()
                self.assertNotEqual(self.sb.read_settings(), raw)
                vault_before = tree(self.sb.vault)
                done = harness.shim(self.sb, "uninstall", "--claude-settings", self.sb.settings)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual(self.sb.read_settings(), raw)
                self.assertEqual(tree(self.sb.vault), vault_before)
                self.assertFalse(os.path.exists(os.path.join(self.sb.bin, "juno")))

    def test_missing_settings_file_is_removed(self) -> None:
        self.init()
        self.assertIsNotNone(self.sb.read_settings())
        done = harness.shim(self.sb, "uninstall")
        self.assertEqual(done.returncode, 0)
        self.assertIsNone(self.sb.read_settings())


@e2e
class CliLifecycle(Case):
    def test_init_doctor_version_through_the_shim(self) -> None:
        self.init()
        version = harness.shim(self.sb, "version")
        self.assertEqual((version.returncode, version.stdout.split()[0]), (0, "juno"))
        doctor = harness.shim(self.sb, "doctor", *self.sb.install_args())
        self.assertEqual(doctor.returncode, 0, doctor.stdout)
        self.assertNotIn("FAIL", doctor.stdout)
        self.assertIn("PASS interpreter", doctor.stdout)


@e2e
class MigrateAtCurrentSchema(Case):
    def test_migrate_at_current_schema(self) -> None:
        self.init()
        done = harness.shim(self.sb, "migrate")
        self.assertEqual(done.returncode, 0)
        self.assertIn("nothing to do", done.stdout)


@e2e
class ExportImport(Case):
    def test_vault_moves_to_a_fresh_machine(self) -> None:
        self.init()
        note = os.path.join(self.sb.vault, "notes", "2026-01-01-moved.md")
        with open(note, "w", encoding="utf-8") as handle:
            handle.write("moved\n")
        tarball = os.path.join(self.sb.root, "juno.tar.gz")
        done = harness.shim(self.sb, "export", "--out", tarball)
        self.assertEqual(done.returncode, 0, done.stdout)
        fresh = Sandbox()
        self.addCleanup(fresh.cleanup)
        result = subprocess.run(
            [harness.JUNO_BIN, "import", tarball, *fresh.install_args()],
            capture_output=True,
            text=True,
            env=fresh.env,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(tree(fresh.vault), tree(self.sb.vault))


if __name__ == "__main__":
    unittest.main()
