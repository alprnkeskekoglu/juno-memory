import contextlib
import importlib.util
import io
import json
import os
import unittest
from typing import Any
from unittest import mock

from tests.support import REPO, SandboxCase, timing

EVENTS = [
    "session_start",
    "subagent_start",
    "user_prompt_submit",
    "pre_tool_use",
    "post_tool_use",
    "post_tool_use_failure",
    "stop",
    "session_end",
]


def load_entry(name: str) -> Any:
    path = os.path.join(REPO, "juno", "hooks", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"entry_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HookCase(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)

    def call(self, event: str, env: dict[str, str], payload: str = "{}") -> tuple[int, str]:
        entry = load_entry(event)
        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch("sys.stdin", io.StringIO(payload)),
            contextlib.redirect_stdout(out),
        ):
            code = entry.hookcore.run(event, entry.handle)
        return code, out.getvalue()

    def interactive(self, **extra: str) -> dict[str, str]:
        env = dict(self.sb.env)
        env["JUNO_TEST_INTERACTIVE"] = "1"
        env.update(extra)
        return env


class SessionStart(HookCase):
    def test_injects_core_and_global_rules(self) -> None:
        with open(
            os.path.join(self.sb.vault, "rules", "global.md"), "w", encoding="utf-8"
        ) as handle:
            handle.write('- 2026-01-01 · Keep answers short · src: user abcd1234 · "quote"\n')
        code, out = self.call("session_start", self.interactive())
        self.assertEqual(code, 0)
        text = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(text["hookEventName"], "SessionStart")
        body = text["additionalContext"]
        self.assertTrue(body.startswith("RULES (follow):"))
        self.assertIn("Juno memory protocol", body)
        self.assertIn("Keep answers short", body)
        self.assertLessEqual(len(body), 9500)

    def test_empty_global_rules_only_core(self) -> None:
        body = json.loads(self.call("session_start", self.interactive())[1])["hookSpecificOutput"][
            "additionalContext"
        ]
        self.assertNotIn("Global rules", body)

    def test_cap_holds_for_huge_files(self) -> None:
        with open(os.path.join(self.sb.vault, "core.md"), "w", encoding="utf-8") as handle:
            handle.write("c" * 50000)
        with open(
            os.path.join(self.sb.vault, "rules", "global.md"), "w", encoding="utf-8"
        ) as handle:
            handle.write("r\n" * 50000)
        body = json.loads(self.call("session_start", self.interactive())[1])["hookSpecificOutput"][
            "additionalContext"
        ]
        self.assertLessEqual(len(body), 9500)
        self.assertIn("Juno truncated", body)

    def test_configured_cap_is_used(self) -> None:
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write("\n[caps]\nhook_output_chars = 1000\n")
        body = json.loads(self.call("session_start", self.interactive())[1])["hookSpecificOutput"][
            "additionalContext"
        ]
        self.assertLessEqual(len(body), 1000)

    def test_headless_gets_nothing(self) -> None:
        for env in (
            dict(self.sb.env, CLAUDE_CODE_SESSION_ATTENDED="0"),
            dict(self.sb.env, CLAUDE_CODE_SESSION_ATTENDED="0", JUNO_TEST_INTERACTIVE="0"),
            dict(self.sb.env),
        ):
            with self.subTest(env=sorted(set(env) - set(self.sb.env))):
                self.assertEqual(self.call("session_start", env), (0, ""))

    def test_override_needs_a_non_default_config(self) -> None:
        default_config = os.path.join(self.sb.home, ".config", "juno", "juno.toml")
        env = self.interactive(JUNO_CONFIG=default_config)
        self.assertEqual(self.call("session_start", env), (0, ""))
        env = self.interactive()
        del env["JUNO_CONFIG"]
        self.assertEqual(self.call("session_start", env), (0, ""))

    def test_attended_session_is_interactive(self) -> None:
        env = dict(self.sb.env, CLAUDE_CODE_SESSION_ATTENDED="1")
        self.assertIn("RULES (follow)", self.call("session_start", env)[1])

    def test_disable_switch_wins(self) -> None:
        env = self.interactive(JUNO_DISABLE="1")
        self.assertEqual(self.call("session_start", env), (0, ""))

    def test_missing_vault_fails_open(self) -> None:
        env = self.interactive(JUNO_VAULT=os.path.join(self.sb.root, "nowhere"))
        self.assertEqual(self.call("session_start", env), (0, ""))

    def test_bad_stdin_and_bad_config_fail_open(self) -> None:
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write("\n[caps\n")
        code, out = self.call("session_start", self.interactive(), payload="not json")
        self.assertEqual(code, 0)
        self.assertIn("RULES (follow)", out)

    def test_handler_exception_is_logged_not_raised(self) -> None:
        entry = load_entry("session_start")

        def boom(payload: dict[str, Any], cfg: Any) -> None:
            raise RuntimeError("handler broke")

        with (
            mock.patch.dict(os.environ, self.interactive(), clear=True),
            mock.patch("sys.stdin", io.StringIO("{}")),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            code = entry.hookcore.run("session_start", boom)
        self.assertEqual((code, out.getvalue()), (0, ""))
        with open(os.path.join(self.sb.state, "hook-errors.log"), encoding="utf-8") as handle:
            self.assertIn("handler broke", handle.read())

    def test_timing_is_logged_for_interactive_runs_only(self) -> None:
        self.call("session_start", dict(self.sb.env, CLAUDE_CODE_SESSION_ATTENDED="0"))
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "hook-timings.log")))
        self.call("session_start", self.interactive())
        with open(os.path.join(self.sb.state, "hook-timings.log"), encoding="utf-8") as handle:
            self.assertRegex(handle.read(), r"session_start \d+ms")


class OtherEvents(HookCase):
    def test_every_event_has_an_entry_that_stays_silent(self) -> None:
        for event in EVENTS[1:]:
            with self.subTest(event):
                self.assertEqual(self.call(event, self.interactive(), "{}"), (0, ""))

    def test_every_event_is_silent_when_headless(self) -> None:
        for event in EVENTS:
            with self.subTest(event):
                env = dict(self.sb.env, CLAUDE_CODE_SESSION_ATTENDED="0")
                self.assertEqual(self.call(event, env), (0, ""))


class Latency(HookCase):
    @timing
    def test_session_start_median_is_under_50ms_in_process(self) -> None:
        import statistics
        import time

        env = self.interactive()
        samples = []
        for _ in range(21):
            started = time.perf_counter()
            self.call("session_start", env)
            samples.append((time.perf_counter() - started) * 1000)
        self.assertLess(statistics.median(samples), 50)


if __name__ == "__main__":
    unittest.main()
