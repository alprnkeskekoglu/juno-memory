import datetime
import os
import plistlib
import stat
import sys
import unittest
from unittest import mock

from juno.cmd import init
from juno.core import schedule, tomlio
from tests.support import SandboxCase

FAKE = """#!{python}
import os, sys
with open(os.environ["FAKE_TOOL_LOG"], "a", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("FAKE_TOOL_CODE", "0")))
"""


class Rendering(unittest.TestCase):
    ARGV = schedule.command("/py/python3", "/plug")

    def test_command_is_isolated_python_on_the_plugin_cli(self) -> None:
        self.assertEqual(
            self.ARGV, ["/py/python3", "-I", "-S", "/plug/juno/cli.py", "stale", "--fix", "--job"]
        )

    def test_plist_has_hourly_sunday_slots_and_only_the_given_paths(self) -> None:
        data = schedule.plist(
            self.ARGV, {"HOME": "/h", "PATH": "/bin"}, "/state/x.log", "sun", 9, 21
        )
        loaded = plistlib.loads(data)
        self.assertEqual(loaded["Label"], "dev.juno.stale-weekly")
        self.assertEqual(loaded["ProgramArguments"], self.ARGV)
        slots = loaded["StartCalendarInterval"]
        self.assertEqual([s["Hour"] for s in slots], list(range(9, 22)))
        self.assertEqual({(s["Weekday"], s["Minute"]) for s in slots}, {(0, 0)})
        self.assertNotIn("/Users/", data.decode())
        self.assertFalse(loaded["RunAtLoad"])

    def test_systemd_units(self) -> None:
        service = schedule.service(self.ARGV, {"HOME": "/h"}, "/state/x.log")
        self.assertIn('ExecStart="/py/python3" "-I" "-S" "/plug/juno/cli.py"', service)
        self.assertIn('Environment="HOME=/h"', service)
        timer = schedule.timer("sun", 9, 21)
        self.assertIn("OnCalendar=Sun *-*-* 09..21:00:00", timer)
        self.assertIn("Persistent=true", timer)
        self.assertNotIn("/Users/", service + timer)

    def test_cron_line_quotes_every_word(self) -> None:
        line = schedule.cron_line(["/p y/python", "-I"], "mon", 9, 21, "/s/x.log")
        self.assertEqual(line, "0 9-21 * * 1 '/p y/python' '-I' >> '/s/x.log' 2>&1")

    def test_window_problems(self) -> None:
        self.assertIsNone(schedule.window_problem("Sun", 9, 21))
        self.assertIn("weekday", schedule.window_problem("someday", 9, 21) or "")
        self.assertIn("hour", schedule.window_problem("sun", 22, 21) or "")

    def test_next_fire(self) -> None:
        saturday = datetime.datetime(2026, 11, 7, 12, 0)
        self.assertEqual(
            schedule.next_fire(saturday, "sun", 9, 21), datetime.datetime(2026, 11, 8, 9, 0)
        )
        sunday_noon = datetime.datetime(2026, 11, 8, 12, 30)
        self.assertEqual(
            schedule.next_fire(sunday_noon, "sun", 9, 21), datetime.datetime(2026, 11, 8, 13, 0)
        )
        late = datetime.datetime(2026, 11, 8, 22, 0)
        self.assertEqual(
            schedule.next_fire(late, "sun", 9, 21), datetime.datetime(2026, 11, 15, 9, 0)
        )


class TomlEdit(unittest.TestCase):
    def test_set_table_bool(self) -> None:
        self.assertEqual(
            tomlio.set_table_bool("", "stale", "auto_fix", True), "[stale]\nauto_fix = true\n"
        )
        base = '[runtime]\npython = "/p"\n'
        added = tomlio.set_table_bool(base, "stale", "auto_fix", True)
        self.assertEqual(added, base + "\n[stale]\nauto_fix = true\n")
        flipped = tomlio.set_table_bool(added + "[other]\nx = 1\n", "stale", "auto_fix", False)
        self.assertIn("[stale]\nauto_fix = false\n[other]", flipped)


class Verb(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)
        self.log = os.path.join(self.sb.root, "tool.log")
        self.tool = os.path.join(self.sb.root, "fake-tool")
        with open(self.tool, "w", encoding="utf-8") as handle:
            handle.write(FAKE.format(python=sys.executable))
        os.chmod(self.tool, os.stat(self.tool).st_mode | stat.S_IXUSR)
        self.sb.env.update(
            JUNO_SCHEDULE_PLATFORM="darwin",
            JUNO_LAUNCHCTL=self.tool,
            JUNO_SYSTEMCTL=self.tool,
            FAKE_TOOL_LOG=self.log,
        )

    def turn_on(self) -> None:
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write("\n[stale]\nauto_fix = true\n")

    def calls(self) -> list[str]:
        try:
            with open(self.log, encoding="utf-8") as handle:
                return handle.read().splitlines()
        except FileNotFoundError:
            return []

    def plist(self) -> str:
        return os.path.join(self.sb.home, "Library", "LaunchAgents", "dev.juno.stale-weekly.plist")

    def test_install_refuses_while_auto_fix_is_off(self) -> None:
        code, out = self.sb.cli("schedule", "install", "stale-weekly")
        self.assertEqual(code, 2)
        self.assertIn("auto_fix", out)
        self.assertFalse(os.path.exists(self.plist()))
        self.assertEqual(self.calls(), [])

    def test_install_refuses_below_schema_two(self) -> None:
        self.turn_on()
        with open(os.path.join(self.sb.vault, "juno-brain.toml"), "w", encoding="utf-8") as handle:
            handle.write("schema_version = 1\n")
        code, out = self.sb.cli("schedule", "install")
        self.assertEqual(code, 2)
        self.assertIn("migrate", out)

    def test_install_writes_the_plist_under_home_and_bootstraps_it(self) -> None:
        self.turn_on()
        code, out = self.sb.cli("schedule", "install", "stale-weekly")
        self.assertEqual(code, 0, out)
        with open(self.plist(), "rb") as handle:
            loaded = plistlib.load(handle)
        self.assertEqual(loaded["ProgramArguments"][-3:], ["stale", "--fix", "--job"])
        env = loaded["EnvironmentVariables"]
        self.assertEqual(env["HOME"], self.sb.home)
        self.assertEqual(env["JUNO_VAULT"], self.sb.vault)
        self.assertTrue(loaded["StandardOutPath"].startswith(self.sb.state))
        self.assertEqual(
            self.calls(),
            [
                f"bootout gui/{os.getuid()}/dev.juno.stale-weekly",
                f"bootstrap gui/{os.getuid()} {self.plist()}",
            ],
        )
        self.assertEqual(self.sb.cli("schedule", "install")[0], 0)

    def test_bootstrap_failure_is_reported(self) -> None:
        self.turn_on()
        self.sb.env["FAKE_TOOL_CODE"] = "5"
        code, out = self.sb.cli("schedule", "install")
        self.assertEqual(code, 2)
        self.assertIn("bootstrap failed", out)

    def test_status_and_remove(self) -> None:
        self.turn_on()
        self.sb.cli("schedule", "install")
        code, out = self.sb.cli("schedule", "status")
        self.assertEqual(code, 0)
        self.assertIn("installed True, loaded True", out)
        self.assertIn("no run yet", out)
        self.assertIn("next fire", out)
        code, out = self.sb.cli("schedule", "remove")
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.plist()))
        self.assertTrue(self.calls()[-1].startswith("bootout"))
        self.assertIn("installed False", self.sb.cli("schedule", "status")[1])

    def test_systemd_branch_writes_user_units(self) -> None:
        self.turn_on()
        self.sb.env["JUNO_SCHEDULE_PLATFORM"] = "linux"
        code, out = self.sb.cli("schedule", "install")
        self.assertEqual(code, 0, out)
        unit = os.path.join(self.sb.home, ".config", "systemd", "user", "juno-stale-weekly.timer")
        self.assertTrue(os.path.exists(unit))
        self.assertEqual(
            self.calls(), ["--user daemon-reload", "--user enable --now juno-stale-weekly.timer"]
        )

    def test_without_systemd_it_prints_the_cron_line(self) -> None:
        self.turn_on()
        self.sb.env["JUNO_SCHEDULE_PLATFORM"] = "linux"
        self.sb.env["PATH"] = self.sb.root
        self.sb.env["JUNO_SYSTEMCTL"] = "no-such-systemctl"
        code, out = self.sb.cli("schedule", "install")
        self.assertEqual(code, 0, out)
        self.assertIn("crontab", out)
        self.assertIn("0 9-21 * * 0", out)
        self.assertEqual(self.calls(), [])


class InitOffer(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.sb.env.update(JUNO_SCHEDULE_PLATFORM="darwin", JUNO_LAUNCHCTL="/bin/echo")
        self.asked: list[str] = []

    def reply(self, prompt: str, answer: str) -> str:
        if "Sunday" not in prompt:
            return ""
        self.asked.append(prompt)
        return answer

    def init(self, tty: bool, answer: str = "") -> tuple[int, str]:
        with (
            mock.patch.object(init, "interactive", lambda: tty),
            mock.patch("builtins.input", lambda prompt: self.reply(prompt, answer)),
        ):
            return self.sb.cli("init", *self.sb.install_args())

    def config_text(self) -> str:
        try:
            with open(self.sb.config, encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return ""

    def test_non_interactive_init_asks_nothing(self) -> None:
        self.assertEqual(self.init(False)[0], 0)
        self.assertEqual(self.asked, [])
        self.assertNotIn("auto_fix", self.config_text())

    def test_interactive_no_leaves_it_off_and_asks_once(self) -> None:
        self.assertEqual(self.init(True, "")[0], 0)
        self.assertEqual(len(self.asked), 1)
        self.assertIn("every Sunday", self.asked[0])
        self.assertIn("$3 a week", self.asked[0])
        self.assertNotIn("auto_fix", self.config_text())
        self.init(True, "y")
        self.assertEqual(len(self.asked), 1)

    def test_interactive_yes_turns_it_on_and_installs(self) -> None:
        code, out = self.init(True, "y")
        self.assertEqual(code, 0, out)
        self.assertIn("auto_fix = true", self.config_text())
        self.assertIn("schedule: loaded", out)
        plist = os.path.join(self.sb.home, "Library", "LaunchAgents", "dev.juno.stale-weekly.plist")
        self.assertTrue(os.path.exists(plist))

    def test_dry_run_asks_nothing(self) -> None:
        with (
            mock.patch.object(init, "interactive", lambda: True),
            mock.patch("builtins.input", lambda prompt: self.reply(prompt, "y")),
        ):
            self.sb.cli("init", "--dry-run", *self.sb.install_args())
        self.assertEqual(self.asked, [])


if __name__ == "__main__":
    unittest.main()
