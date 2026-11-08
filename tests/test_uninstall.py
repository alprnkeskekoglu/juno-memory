import json
import os
import unittest

from tests.support import SandboxCase, settings_variants
from tests.test_init import tree


class ByteIdentical(SandboxCase):
    def roundtrip(self, raw: bytes | None) -> None:
        if raw is not None:
            self.sb.write_settings(raw)
        code, out = self.sb.cli("init", *self.sb.install_args())
        self.assertEqual(code, 0, out)
        self.assertNotEqual(self.sb.read_settings(), raw)
        vault_before = tree(self.sb.vault)
        code, out = self.sb.cli("uninstall", *self.sb.install_args())
        self.assertEqual(code, 0, out)
        self.assertEqual(self.sb.read_settings(), raw)
        self.assertEqual(tree(self.sb.vault), vault_before)
        self.assertFalse(os.path.exists(os.path.join(self.sb.bin, "juno")))
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "install-manifest.json")))

    def test_every_settings_variant(self) -> None:
        for name, raw in settings_variants().items():
            with self.subTest(name):
                self.setUp()
                self.roundtrip(raw)
                self.sb.cleanup()

    def test_missing_settings_file_is_removed_again(self) -> None:
        self.roundtrip(None)

    def test_file_mode_survives(self) -> None:
        self.sb.write_settings(settings_variants()["indent2"])
        os.chmod(self.sb.settings, 0o640)
        self.sb.cli("init", *self.sb.install_args())
        self.sb.cli("uninstall", *self.sb.install_args())
        self.assertEqual(os.stat(self.sb.settings).st_mode & 0o777, 0o640)

    def test_reinit_then_uninstall_still_identical(self) -> None:
        raw = settings_variants()["indent4"]
        self.sb.write_settings(raw)
        self.sb.cli("init", *self.sb.install_args())
        self.sb.cli("init", *self.sb.install_args())
        self.sb.cli("uninstall", *self.sb.install_args())
        self.assertEqual(self.sb.read_settings(), raw)

    def test_uninstall_without_claude_settings_option_uses_manifest_path(self) -> None:
        raw = settings_variants()["tabs"]
        self.sb.write_settings(raw)
        self.sb.cli("init", *self.sb.install_args())
        self.assertEqual(self.sb.cli("uninstall")[0], 0)
        self.assertEqual(self.sb.read_settings(), raw)


class AfterUserEdits(SandboxCase):
    def test_removes_only_recorded_entries(self) -> None:
        raw = settings_variants()["indent2"]
        self.sb.write_settings(raw)
        self.sb.cli("init", *self.sb.install_args())
        data = json.loads(self.sb.read_settings() or b"")
        data["permissions"]["allow"].append("Bash(make:*)")
        data["model"] = "haiku"
        self.sb.write_settings(json.dumps(data, indent=2).encode() + b"\n")
        code, out = self.sb.cli("uninstall", *self.sb.install_args())
        self.assertEqual(code, 0)
        self.assertIn("recorded entries", out)
        after = json.loads(self.sb.read_settings() or b"")
        original = json.loads(raw)
        self.assertEqual(after["model"], "haiku")
        self.assertEqual(
            after["permissions"]["allow"], original["permissions"]["allow"] + ["Bash(make:*)"]
        )
        self.assertEqual(after["permissions"]["deny"], original["permissions"]["deny"])
        self.assertNotIn("autoMemoryEnabled", after)

    def test_missing_backup_falls_back_to_removal(self) -> None:
        raw = settings_variants()["indent2"]
        self.sb.write_settings(raw)
        self.sb.cli("init", *self.sb.install_args())
        backups = os.path.join(self.sb.state, "install-backups")
        for name in os.listdir(backups):
            os.unlink(os.path.join(backups, name))
        code, out = self.sb.cli("uninstall", *self.sb.install_args())
        self.assertEqual(code, 0)
        self.assertIn("backup is missing", out)
        self.assertEqual(json.loads(self.sb.read_settings() or b""), json.loads(raw))

    def test_foreign_shim_is_left_alone(self) -> None:
        self.sb.cli("init", *self.sb.install_args())
        target = os.path.join(self.sb.bin, "juno")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\necho mine\n")
        self.sb.cli("uninstall", *self.sb.install_args())
        self.assertTrue(os.path.exists(target))

    def test_dry_run_changes_nothing(self) -> None:
        self.sb.write_settings(settings_variants()["indent2"])
        self.sb.cli("init", *self.sb.install_args())
        before = (self.sb.read_settings(), tree(self.sb.state), os.listdir(self.sb.bin))
        code, out = self.sb.cli("uninstall", "--dry-run", *self.sb.install_args())
        self.assertEqual(code, 0)
        self.assertIn("would", out)
        self.assertEqual(
            (self.sb.read_settings(), tree(self.sb.state), os.listdir(self.sb.bin)), before
        )

    def test_nothing_to_uninstall(self) -> None:
        code, out = self.sb.cli("uninstall")
        self.assertEqual(code, 0)
        self.assertIn("nothing to uninstall", out)


if __name__ == "__main__":
    unittest.main()
