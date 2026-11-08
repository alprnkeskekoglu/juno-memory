import os
import time
import unittest

from juno.adapters import agentcalls, prune
from juno.handlers import post_tool_use, post_tool_use_failure, pre_tool_use
from tests.support.write import WriteCase

QUOTE = "always run the formatter before every commit"
RULE = "Run the formatter before every commit"


class SubagentCalls(WriteCase):
    def payload(self, **over: object) -> dict[str, object]:
        base: dict[str, object] = {
            "session_id": self.sid,
            "tool_use_id": "toolu_01abc",
            "tool_name": "Bash",
            "tool_input": {"command": "juno rule list"},
            "agent_id": "agent-1",
            "cwd": self.sb.root,
        }
        base.update(over)
        return base

    def add(self) -> tuple[int, str]:
        self.prompt(QUOTE, "pid00002")
        return self.juno("rule", "add", "--scope", "global", "--quote", QUOTE, RULE)

    def test_a_subagent_bash_call_in_flight_refuses_writes(self) -> None:
        pre_tool_use.handle(self.payload(), self.cfg)
        code, out = self.add()
        self.assertEqual(code, 2)
        self.assertEqual(out, "Juno: refused: agent context: a subagent tool call is in flight\n")
        self.assertEqual(self.get("rules/global.md").count("\n- "), 0)

    def test_post_tool_use_and_failure_clear_the_marker(self) -> None:
        pre_tool_use.handle(self.payload(), self.cfg)
        post_tool_use.handle(self.payload(tool_response={}), self.cfg)
        self.assertFalse(agentcalls.active(self.cfg, self.sid))
        pre_tool_use.handle(self.payload(), self.cfg)
        post_tool_use_failure.handle(self.payload(error="exit code 1"), self.cfg)
        self.assertFalse(agentcalls.active(self.cfg, self.sid))
        self.assertEqual(self.add()[0], 0)

    def test_main_session_calls_and_other_tools_leave_no_marker(self) -> None:
        pre_tool_use.handle(self.payload(agent_id=None), self.cfg)
        pre_tool_use.handle(
            self.payload(tool_name="Read", tool_input={"file_path": "/x"}), self.cfg
        )
        self.assertFalse(agentcalls.active(self.cfg, self.sid))
        self.assertFalse(os.path.exists(agentcalls.directory(self.cfg)))

    def test_stale_markers_expire_and_other_sessions_do_not_count(self) -> None:
        pre_tool_use.handle(self.payload(), self.cfg)
        later = agentcalls.TTL_SECONDS + 1.0
        self.assertFalse(agentcalls.active(self.cfg, self.sid, time.time() + later))
        self.assertFalse(agentcalls.active(self.cfg, "ffffffff-0000-4000-8000-000000000000"))

    def test_read_only_verbs_stay_available_to_the_subagent(self) -> None:
        pre_tool_use.handle(self.payload(), self.cfg)
        self.assertEqual(self.juno("rule", "list")[0], 0)
        self.assertEqual(self.juno("backlog", "list")[0], 0)

    def test_unwritable_marker_dir_fails_closed_for_a_subagent_call(self) -> None:
        os.makedirs(self.sb.state, exist_ok=True)
        with open(agentcalls.directory(self.cfg), "w", encoding="utf-8") as handle:
            handle.write("not a directory")
        out = pre_tool_use.handle(self.payload(), self.cfg)
        assert out is not None
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIsNone(pre_tool_use.handle(self.payload(agent_id=None), self.cfg))
        read = self.payload(tool_name="Read", tool_input={"file_path": "/x"})
        self.assertIsNone(pre_tool_use.handle(read, self.cfg))

    def test_background_calls_keep_their_marker_after_the_tool_returns(self) -> None:
        bg = {"command": "sleep 300 && juno note x", "run_in_background": True}
        pre_tool_use.handle(self.payload(tool_input=bg), self.cfg)
        post_tool_use.handle(self.payload(tool_input=bg, tool_response={}), self.cfg)
        self.assertTrue(agentcalls.active(self.cfg, self.sid))
        self.assertEqual(self.add()[0], 2)

    def test_marker_lives_as_long_as_the_tool_timeout_not_the_full_ttl(self) -> None:
        pre_tool_use.handle(self.payload(), self.cfg)
        soon = time.time() + agentcalls.DEFAULT_TIMEOUT
        self.assertTrue(agentcalls.active(self.cfg, self.sid, soon))
        late = time.time() + agentcalls.DEFAULT_TIMEOUT + agentcalls.GRACE_SECONDS + 5
        self.assertFalse(agentcalls.active(self.cfg, self.sid, late))
        long_call = {"command": "make", "timeout": 600000}
        pre_tool_use.handle(self.payload(tool_use_id="toolu_02", tool_input=long_call), self.cfg)
        self.assertTrue(agentcalls.active(self.cfg, self.sid, late))

    def test_orphans_are_reaped_by_the_next_post_hook_and_by_prune(self) -> None:
        pre_tool_use.handle(self.payload(tool_use_id="toolu_orphan"), self.cfg)
        path = agentcalls.marker(self.cfg, self.payload(tool_use_id="toolu_orphan"))
        assert path is not None
        old = time.time() - agentcalls.TTL_SECONDS - 5
        os.utime(path, (old, old))
        with open(path, "w", encoding="ascii") as handle:
            handle.write(str(old))
        post_tool_use.handle(self.payload(tool_use_id="toolu_other", tool_response={}), self.cfg)
        self.assertFalse(os.path.exists(path))
        with open(path, "w", encoding="ascii") as handle:
            handle.write(str(old))
        prune.prune_state(self.cfg)
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
