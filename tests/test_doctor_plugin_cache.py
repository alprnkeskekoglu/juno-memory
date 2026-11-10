import json
import os
import shutil
import subprocess
import tempfile
import unittest
from typing import Any

from juno.cmd import doctor

IDENT = ["-c", "user.email=t@t", "-c", "user.name=t"]


class FakeCfg:
    def __init__(self, root: str) -> None:
        self.root = root
        self.env = dict(os.environ)

    def get_str(self, section: str, key: str) -> str:
        return self.root


class PluginCache(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="juno-pc-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "checkout")
        for sub, name in (("", "juno"), ("ui", "juno-ui")):
            meta = os.path.join(self.root, sub, ".claude-plugin")
            os.makedirs(meta)
            with open(os.path.join(meta, "plugin.json"), "w", encoding="utf-8") as h:
                json.dump({"name": name, "version": "1.0.4"}, h)
        self.vcs("init", "-q")
        self.vcs("add", "-A")
        self.vcs(*IDENT, "commit", "-q", "-m", "x")
        self.head = self.vcs("rev-parse", "HEAD")
        self.settings = os.path.join(self.tmp, "claude", "settings.json")

    def vcs(self, *args: str) -> str:
        done = subprocess.run(
            ["git", "-C", self.root, *args], capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    def record(self, plugins: dict[str, Any]) -> list[tuple[str, str, str]]:
        folder = os.path.join(self.tmp, "claude", "plugins")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "installed_plugins.json"), "w", encoding="utf-8") as h:
            json.dump({"version": 2, "plugins": plugins}, h)
        return doctor.check_plugin_cache(FakeCfg(self.root), self.settings)  # type: ignore[arg-type]

    def entry(self, version: str, sha: str, path: str = "/cache/x") -> list[dict[str, str]]:
        return [{"scope": "user", "installPath": path, "version": version, "gitCommitSha": sha}]

    def test_nothing_installed_prints_nothing(self) -> None:
        self.assertEqual(self.record({}), [])
        self.assertEqual(self.record({"other@x": self.entry("1", "a")}), [])

    def test_matching_passes_and_ui_only_when_installed(self) -> None:
        out = self.record({"juno@juno-local": self.entry("1.0.4", self.head)})
        self.assertEqual([c[:2] for c in out], [("PASS", "plugin cache")])
        out = self.record(
            {
                "juno@juno-local": self.entry("1.0.4", self.head),
                "juno-ui@juno-local": self.entry("1.0.4", self.head),
            }
        )
        self.assertEqual([c[0] for c in out], ["PASS", "PASS"])

    def test_older_version_warns_with_update_command(self) -> None:
        out = self.record({"juno@juno-local": self.entry("1.0.3", "c533a8b" + "0" * 33)})
        self.assertEqual(
            out,
            [
                (
                    "WARN",
                    "plugin cache",
                    f"cache for juno@juno-local is at 1.0.3 (c533a8b), checkout is 1.0.4 "
                    f"({self.head[:7]}); run claude plugin update juno@juno-local",
                )
            ],
        )

    def test_same_version_other_sha_suggests_reinstall(self) -> None:
        out = self.record({"juno-ui@juno-local": self.entry("1.0.4", "c533a8b" + "0" * 33)})
        self.assertEqual(out[0][0], "WARN")
        self.assertIn(
            "run claude plugin uninstall juno-ui@juno-local && "
            "claude plugin install juno-ui@juno-local",
            out[0][2],
        )

    def test_directory_sourced_install_is_not_flagged(self) -> None:
        out = self.record(
            {
                "juno@juno-local": self.entry("0.1.0", "", self.root),
                "juno-ui@juno-local": self.entry("0.1.0", "", os.path.join(self.root, "ui")),
            }
        )
        self.assertEqual([c[0] for c in out], ["PASS", "PASS"])
        self.assertIn("reads straight from", out[0][2])

    def test_not_a_checkout_is_info_not_crash(self) -> None:
        shutil.rmtree(os.path.join(self.root, ".git"))
        out = self.record({"juno@juno-local": self.entry("1.0.4", "c533a8b")})
        self.assertEqual(out[0][0], "INFO")
        out = self.record({"juno@juno-local": self.entry("1.0.3", "c533a8b")})
        self.assertEqual(out[0][0], "WARN")
        self.assertIn("claude plugin update", out[0][2])

    def test_binary_missing_is_info(self) -> None:
        self.record({"juno@juno-local": self.entry("1.0.4", self.head)})
        cfg = FakeCfg(self.root)
        cfg.env = {"PATH": os.path.join(self.tmp, "nothing")}
        out = doctor.check_plugin_cache(cfg, self.settings)  # type: ignore[arg-type]
        self.assertEqual(out[0][0], "INFO")

    def test_garbage_record_does_not_crash(self) -> None:
        folder = os.path.join(self.tmp, "claude", "plugins")
        os.makedirs(folder)
        with open(os.path.join(folder, "installed_plugins.json"), "w", encoding="utf-8") as h:
            h.write("{not json")
        out = doctor.check_plugin_cache(FakeCfg(self.root), self.settings)  # type: ignore[arg-type]
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
