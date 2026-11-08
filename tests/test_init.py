import json
import os
import stat
import tomllib
import unittest
from unittest import mock

from juno.core import shim
from tests.support import SandboxCase, realistic_settings


def tree(root: str) -> dict[str, bytes]:
    found = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                found[os.path.relpath(path, root)] = handle.read()
    return found


class Init(SandboxCase):
    def init(self, *extra: str) -> tuple[int, str]:
        return self.sb.cli("init", *self.sb.install_args(), *extra)

    def test_creates_everything(self) -> None:
        self.sb.write_settings(json.dumps(realistic_settings(), indent=2).encode() + b"\n")
        code, out = self.init()
        self.assertEqual(code, 0, out)
        for line in (
            "interpreter:",
            "config:",
            "state:",
            "vault:",
            "settings:",
            "shim:",
            "plugin:",
        ):
            self.assertIn(line, out)
        with open(self.sb.config, encoding="utf-8") as handle:
            runtime = tomllib.loads(handle.read())["runtime"]
        self.assertTrue(os.access(runtime["python"], os.X_OK))
        self.assertTrue(os.path.isfile(os.path.join(runtime["plugin_root"], "juno", "cli.py")))
        self.assertTrue(os.path.isdir(os.path.join(self.sb.vault, ".git")))
        self.assertEqual(stat.S_IMODE(os.stat(self.sb.state).st_mode), 0o700)
        shim_path = os.path.join(self.sb.bin, "juno")
        self.assertTrue(os.access(shim_path, os.X_OK))
        with open(shim_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), shim.SHIM)
        settings = json.loads(self.sb.read_settings() or b"")
        self.assertIs(settings["autoMemoryEnabled"], False)
        self.assertIn(self.sb.vault, settings["permissions"]["additionalDirectories"])
        self.assertIn(f"Edit(/{self.sb.vault}/rules/**)", settings["permissions"]["deny"])
        self.assertEqual(settings["hooks"], realistic_settings()["hooks"])

    def test_vault_has_template_content_and_schema(self) -> None:
        self.init()
        with open(os.path.join(self.sb.vault, "juno-brain.toml"), "rb") as handle:
            data = tomllib.load(handle)
        self.assertEqual(data["schema_version"], 1)
        self.assertRegex(str(data["created"]), r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(os.path.isfile(os.path.join(self.sb.vault, "core.md")))
        self.assertEqual(os.path.getsize(os.path.join(self.sb.vault, "rules", "global.md")), 0)

    def test_idempotent(self) -> None:
        self.init()
        before = (tree(self.sb.vault), self.sb.read_settings())
        with open(os.path.join(self.sb.state, "install-manifest.json"), "rb") as handle:
            manifest = handle.read()
        code, out = self.init()
        self.assertEqual(code, 0)
        self.assertIn("already merged", out)
        self.assertEqual((tree(self.sb.vault), self.sb.read_settings()), before)
        with open(os.path.join(self.sb.state, "install-manifest.json"), "rb") as handle:
            self.assertEqual(handle.read(), manifest)

    def test_never_overwrites_an_existing_vault(self) -> None:
        self.init()
        core = os.path.join(self.sb.vault, "core.md")
        with open(core, "w", encoding="utf-8") as handle:
            handle.write("my own protocol\n")
        os.unlink(os.path.join(self.sb.vault, "protocol", "links.md"))
        code, out = self.init()
        self.assertEqual(code, 0)
        self.assertIn("left untouched", out)
        with open(core, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "my own protocol\n")
        self.assertFalse(os.path.exists(os.path.join(self.sb.vault, "protocol", "links.md")))

    def test_foreign_vault_refused(self) -> None:
        os.makedirs(self.sb.vault)
        with open(os.path.join(self.sb.vault, "core.md"), "w", encoding="utf-8") as handle:
            handle.write("v1 vault\n")
        code, out = self.init()
        self.assertEqual(code, 2)
        self.assertIn("refusing", out)
        self.assertIsNone(self.sb.read_settings())

    def test_empty_vault_dir_is_populated(self) -> None:
        os.makedirs(self.sb.vault)
        self.assertEqual(self.init()[0], 0)
        self.assertTrue(os.path.isfile(os.path.join(self.sb.vault, "core.md")))

    def test_older_schema_points_to_migrate(self) -> None:
        self.init()
        with open(os.path.join(self.sb.vault, "juno-brain.toml"), "w", encoding="utf-8") as handle:
            handle.write("schema_version = 0\n")
        code, out = self.init()
        self.assertEqual(code, 2)
        self.assertIn("juno migrate", out)

    def test_newer_schema_refused(self) -> None:
        self.init()
        with open(os.path.join(self.sb.vault, "juno-brain.toml"), "w", encoding="utf-8") as handle:
            handle.write("schema_version = 7\n")
        self.assertEqual(self.init()[0], 2)

    def test_dry_run_writes_nothing(self) -> None:
        before = set(os.listdir(self.sb.root))
        code, out = self.init("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would", out)
        self.assertIn('+  "autoMemoryEnabled": false', out)
        self.assertEqual(set(os.listdir(self.sb.root)), before)

    def test_existing_config_keys_are_kept(self) -> None:
        os.makedirs(os.path.dirname(self.sb.config))
        with open(self.sb.config, "w", encoding="utf-8") as handle:
            handle.write("[caps]\ncore_chars = 3000\n")
        self.init()
        with open(self.sb.config, encoding="utf-8") as handle:
            text = handle.read()
        self.assertTrue(text.startswith("[runtime]"))
        self.assertIn("core_chars = 3000", text)

    def test_foreign_shim_is_not_replaced(self) -> None:
        os.makedirs(self.sb.bin)
        target = os.path.join(self.sb.bin, "juno")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\necho mine\n")
        code, out = self.init()
        self.assertEqual(code, 2)
        self.assertIn("not Juno's", out)
        with open(target, encoding="utf-8") as handle:
            self.assertIn("echo mine", handle.read())
        self.assertIn("settings: merged", out)

    def test_non_strict_settings_refused_untouched(self) -> None:
        raw = b'{"model": "opus",}\n'
        self.sb.write_settings(raw)
        code, out = self.init()
        self.assertEqual(code, 2)
        self.assertIn("not strict JSON", out)
        self.assertEqual(self.sb.read_settings(), raw)

    def test_interpreter_below_floor_refused(self) -> None:
        fake = os.path.join(self.sb.root, "py39")
        with open(fake, "w", encoding="utf-8") as handle:
            handle.write('#!/bin/sh\necho "(3, 9)"\n')
        os.chmod(fake, 0o755)
        self.sb.env["JUNO_PYTHON"] = fake
        code, out = self.init()
        self.assertEqual(code, 2)
        self.assertIn("needs Python >= 3.14", out)
        self.assertFalse(os.path.exists(self.sb.config))

    def test_plugin_hint_when_not_installed(self) -> None:
        self.assertIn("claude plugin install juno@juno-local", self.init()[1])

    def test_plugin_detected_when_installed(self) -> None:
        plugins = os.path.join(os.path.dirname(self.sb.settings), "plugins")
        os.makedirs(plugins)
        with open(os.path.join(plugins, "installed_plugins.json"), "w", encoding="utf-8") as handle:
            json.dump({"plugins": {"juno@juno-local": [{"installPath": "/x"}]}}, handle)
        self.assertIn("plugin: juno is installed", self.init()[1])


if __name__ == "__main__":
    unittest.main()


class InitUnderGitHook(SandboxCase):
    def test_vault_git_ignores_inherited_git_env(self) -> None:
        decoy = os.path.join(self.sb.root, "decoy.git")
        with mock.patch.dict(os.environ, {"GIT_DIR": decoy, "GIT_INDEX_FILE": decoy + "/index"}):
            code, out = self.sb.cli("init", *self.sb.install_args())
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.isdir(os.path.join(self.sb.vault, ".git")))
        self.assertFalse(os.path.exists(decoy))
