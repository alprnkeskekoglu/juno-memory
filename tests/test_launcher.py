import json
import os
import subprocess
import unittest

from juno.core import tomlio
from tests.support import REPO, SandboxCase, subproc

HOOK = os.path.join(REPO, "bin", "juno-hook")
JUNO = os.path.join(REPO, "bin", "juno")


def run(
    argv: list[str], env: dict[str, str], stdin: str = "{}"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, env=env, input=stdin, timeout=60)


@subproc
class HookLauncher(SandboxCase):
    def interactive(self) -> dict[str, str]:
        return dict(self.sb.env, JUNO_TEST_INTERACTIVE="1")

    def init(self) -> None:
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)

    def test_without_config_session_start_prints_the_init_hint(self) -> None:
        done = run([HOOK, "session_start"], self.sb.env)
        self.assertEqual(done.returncode, 0)
        text = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(text, f'Juno is not initialised: run "{REPO}/bin/juno" init')

    def test_without_config_other_events_are_silent(self) -> None:
        for event in ("stop", "pre_tool_use", "user_prompt_submit", "session_end"):
            done = run([HOOK, event], self.sb.env)
            self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))

    def test_hint_is_skipped_for_headless_and_disabled(self) -> None:
        for extra in ({"CLAUDE_CODE_SESSION_ATTENDED": "0"}, {"JUNO_DISABLE": "1"}):
            done = run([HOOK, "session_start"], dict(self.sb.env, **extra))
            self.assertEqual((done.returncode, done.stdout), (0, ""))

    def test_injects_when_initialised_and_interactive(self) -> None:
        self.init()
        done = run([HOOK, "session_start"], self.interactive())
        self.assertEqual(done.returncode, 0)
        body = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Juno memory protocol", body)
        self.assertLessEqual(len(body), 9500)

    def test_headless_injects_nothing_and_writes_nothing(self) -> None:
        self.init()
        before = sorted(os.listdir(self.sb.state))
        done = run([HOOK, "session_start"], dict(self.sb.env, CLAUDE_CODE_SESSION_ATTENDED="0"))
        self.assertEqual((done.returncode, done.stdout), (0, ""))
        self.assertEqual(sorted(os.listdir(self.sb.state)), before)

    def test_unusable_interpreter_exits_zero_silently(self) -> None:
        self.init()
        env = dict(self.interactive(), JUNO_PYTHON=os.path.join(self.sb.root, "missing-python"))
        done = run([HOOK, "session_start"], env)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))

    def test_failing_script_still_exits_zero(self) -> None:
        self.init()
        fake = os.path.join(self.sb.root, "failing-python")
        with open(fake, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nexit 7\n")
        os.chmod(fake, 0o755)
        done = run([HOOK, "stop"], dict(self.interactive(), JUNO_PYTHON=fake))
        self.assertEqual(done.returncode, 0)

    def test_bad_event_names_do_nothing(self) -> None:
        for event in ("", "../x", "Stop", "a b", "session_start;id"):
            done = run([HOOK, event], self.sb.env)
            self.assertEqual((done.returncode, done.stdout), (0, ""), event)

    def test_unknown_event_does_nothing(self) -> None:
        self.init()
        self.assertEqual(run([HOOK, "no_such_event"], self.interactive()).stdout, "")

    def test_reads_interpreter_with_the_documented_sed(self) -> None:
        self.init()
        with open(self.sb.config, encoding="utf-8") as handle:
            text = handle.read()
        sed = subprocess.run(
            ["sed", "-n", 's/^python = "\\(.*\\)"$/\\1/p', self.sb.config],
            capture_output=True,
            text=True,
        ).stdout.splitlines()[0]
        self.assertEqual(sed, tomlio.read_key(text, "python"))


@subproc
class CliLaunchers(SandboxCase):
    def test_bin_juno_runs_without_a_config(self) -> None:
        done = run([JUNO, "version"], self.sb.env)
        self.assertEqual((done.returncode, done.stdout.split()[0]), (0, "juno"))

    def test_bin_juno_init_then_shim_resolves(self) -> None:
        done = run([JUNO, "init", *self.sb.install_args()], self.sb.env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        shim = os.path.join(self.sb.bin, "juno")
        done = run([shim, "version"], self.sb.env)
        self.assertEqual((done.returncode, done.stdout.split()[0]), (0, "juno"))

    def test_shim_falls_back_to_installed_plugins_record(self) -> None:
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)
        with open(self.sb.config, encoding="utf-8") as handle:
            text = handle.read()
        text = tomlio.set_runtime(
            text, tomlio.read_key(text, "python") or "", "/nonexistent/old/root"
        )
        with open(self.sb.config, "w", encoding="utf-8") as handle:
            handle.write(text)
        claude = os.path.join(self.sb.root, "claude-config")
        os.makedirs(os.path.join(claude, "plugins"))
        record = {"plugins": {"juno@juno-local": [{"installPath": REPO}], "other@x": []}}
        with open(
            os.path.join(claude, "plugins", "installed_plugins.json"), "w", encoding="utf-8"
        ) as h:
            json.dump(record, h)
        shim = os.path.join(self.sb.bin, "juno")
        done = run([shim, "version"], dict(self.sb.env, CLAUDE_CONFIG_DIR=claude))
        self.assertEqual((done.returncode, done.stdout.split()[0]), (0, "juno"), done.stderr)

    def test_shim_reports_missing_plugin(self) -> None:
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)
        with open(self.sb.config, encoding="utf-8") as handle:
            text = handle.read()
        text = tomlio.set_runtime(
            text, tomlio.read_key(text, "python") or "", "/nonexistent/old/root"
        )
        with open(self.sb.config, "w", encoding="utf-8") as handle:
            handle.write(text)
        done = run(
            [os.path.join(self.sb.bin, "juno"), "version"],
            dict(self.sb.env, CLAUDE_CONFIG_DIR="/nope"),
        )
        self.assertEqual(done.returncode, 2)
        self.assertIn("plugin not found", done.stderr)

    def test_cli_script_guards_the_python_version(self) -> None:
        done = subprocess.run(
            ["/usr/bin/python3", "-I", "-S", os.path.join(REPO, "juno", "cli.py"), "version"],
            capture_output=True,
            text=True,
        )
        if done.returncode == 0:
            self.skipTest("system python is new enough")
        self.assertEqual(done.returncode, 2)
        self.assertIn("3.14", done.stderr)


if __name__ == "__main__":
    unittest.main()
