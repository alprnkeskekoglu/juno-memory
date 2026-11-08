import json
import os
import shutil
import subprocess
import tomllib
import unittest

from juno import __version__
from tests.support import REPO, subproc

EVENTS = {
    "SessionStart": "session_start",
    "SubagentStart": "subagent_start",
    "UserPromptSubmit": "user_prompt_submit",
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "PostToolUseFailure": "post_tool_use_failure",
    "Stop": "stop",
    "SessionEnd": "session_end",
}


def load(*parts: str) -> dict[str, object]:
    with open(os.path.join(REPO, *parts), encoding="utf-8") as handle:
        return dict(json.load(handle))


class Manifests(unittest.TestCase):
    def test_plugin_json(self) -> None:
        plugin = load(".claude-plugin", "plugin.json")
        self.assertEqual(plugin["name"], "juno")
        self.assertEqual(plugin["version"], __version__)
        self.assertEqual(plugin["license"], "MIT")

    def test_versions_agree(self) -> None:
        with open(os.path.join(REPO, "pyproject.toml"), "rb") as handle:
            self.assertEqual(tomllib.load(handle)["project"]["version"], __version__)
        market = load(".claude-plugin", "marketplace.json")
        entry = market["plugins"][0]  # type: ignore[index]
        self.assertEqual(
            (entry["name"], entry["version"], entry["source"]), ("juno", __version__, "./")
        )

    def test_ui_mod_is_listed_with_its_own_manifest_version(self) -> None:
        entries = {e["name"]: e for e in load(".claude-plugin", "marketplace.json")["plugins"]}  # type: ignore[attr-defined]
        ui = load("ui", ".claude-plugin", "plugin.json")
        self.assertEqual((entries["juno-ui"]["source"], ui["name"]), ("./ui", "juno-ui"))
        self.assertEqual(entries["juno-ui"]["version"], ui["version"])
        self.assertTrue(os.path.isfile(os.path.join(REPO, "ui", "hooks", "hooks.json")))

    def test_marketplace_name_matches_init_hint(self) -> None:
        from juno.cmd import common

        self.assertEqual(load(".claude-plugin", "marketplace.json")["name"], common.MARKETPLACE)


class HooksJson(unittest.TestCase):
    def test_all_events_wired_through_the_launcher(self) -> None:
        hooks = load("hooks", "hooks.json")["hooks"]
        self.assertEqual(set(hooks), set(EVENTS))  # type: ignore[arg-type]
        for event, name in EVENTS.items():
            groups = hooks[event]  # type: ignore[index]
            self.assertEqual(len(groups), 1)
            handler = groups[0]["hooks"][0]
            self.assertEqual(handler["type"], "command")
            self.assertEqual(handler["command"], f'"${{CLAUDE_PLUGIN_ROOT}}/bin/juno-hook" {name}')
            self.assertTrue(os.path.isfile(os.path.join(REPO, "juno", "hooks", f"{name}.py")))

    def test_timeouts_follow_the_spec(self) -> None:
        hooks = load("hooks", "hooks.json")["hooks"]
        expected = {
            "SessionStart": 5,
            "SubagentStart": 5,
            "UserPromptSubmit": 3,
            "PreToolUse": 3,
            "PostToolUse": 5,
            "PostToolUseFailure": 3,
            "Stop": 3,
        }
        for event, seconds in expected.items():
            self.assertEqual(hooks[event][0]["hooks"][0]["timeout"], seconds)  # type: ignore[index]
        self.assertLessEqual(hooks["SessionEnd"][0]["hooks"][0]["timeout"], 2)  # type: ignore[index]

    def test_matchers(self) -> None:
        hooks = load("hooks", "hooks.json")["hooks"]
        self.assertIn("AskUserQuestion", hooks["PreToolUse"][0]["matcher"])  # type: ignore[index]
        self.assertEqual(hooks["PostToolUse"][0]["matcher"], "*")  # type: ignore[index]
        self.assertEqual(hooks["PostToolUseFailure"][0]["matcher"], "*")  # type: ignore[index]
        self.assertNotIn("matcher", hooks["SessionStart"][0])  # type: ignore[index]


class Executables(unittest.TestCase):
    def test_bin_scripts_are_posix_sh_and_executable(self) -> None:
        for name in ("juno", "juno-hook"):
            path = os.path.join(REPO, "bin", name)
            self.assertTrue(os.access(path, os.X_OK), name)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.readline(), "#!/bin/sh\n")

    def test_bin_scripts_parse_as_sh(self) -> None:
        for name in ("juno", "juno-hook"):
            done = subprocess.run(
                ["/bin/sh", "-n", os.path.join(REPO, "bin", name)], capture_output=True
            )
            self.assertEqual(done.returncode, 0, name)

    def test_shim_text_parses_as_sh(self) -> None:
        from juno.core import shim

        done = subprocess.run(["/bin/sh", "-n"], input=shim.SHIM.encode(), capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr)


@subproc
class Validate(unittest.TestCase):
    def test_claude_plugin_validate_passes(self) -> None:
        if shutil.which("claude") is None:
            self.skipTest("claude CLI not installed")
        done = subprocess.run(
            ["claude", "plugin", "validate", REPO],
            capture_output=True,
            text=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("Validation passed", done.stdout)


if __name__ == "__main__":
    unittest.main()
