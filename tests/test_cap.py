import unittest

from juno.adapters import ledgerio, writelog
from tests.test_writegate_cli import QUOTE, Base


class CapRaise(Base):
    def fill(self, count: int) -> None:
        for n in range(count):
            writelog.append(self.cfg, {"id": f"w-{n:08x}", "sid": self.sid, "verb": "note"})

    def test_cap_is_configurable_and_the_refusal_names_the_way_out(self) -> None:
        self.set_config("[caps]\nrun_max = 3\n")
        self.fill(3)
        out = self.refused("capture", "Yet another loose thought here")
        self.assertIn("cap is 3", out)
        self.assertIn('juno cap raise --by N --evidence "<user\'s words>"', out)
        self.assertIn("[caps] run_max", out)
        self.assertNotIn("ask the user", out)

    def test_raise_needs_the_users_own_words(self) -> None:
        self.set_config("[caps]\nrun_max = 3\n")
        self.refused("cap", "raise", "--by", "5", "--evidence", "repo:app")
        self.refused("cap", "raise", "--by", "5", "--evidence", "too short")
        unsaid = "words nobody typed in this segment"
        self.refused("cap", "raise", "--by", "5", "--evidence", unsaid)
        self.refused("cap", "raise", "--by", "0", "--evidence", QUOTE)
        self.refused("cap", "raise", "--by", "-2", "--evidence", QUOTE)
        self.refused("cap", "raise", "--by", "101", "--evidence", QUOTE)
        self.assertIn("limit 3", self.ok("cap", "show"))

    def test_approved_raise_lifts_this_segments_cap_only(self) -> None:
        self.set_config("[caps]\nrun_max = 3\n")
        self.fill(3)
        self.refused("capture", "Yet another loose thought here")
        before = len(writelog.read(self.cfg))
        self.ok("cap", "raise", "--by", "2", "--evidence", QUOTE)
        self.assertEqual(len(writelog.read(self.cfg)), before)
        self.assertIn("3 used, limit 5", self.ok("cap", "show"))
        self.ok("capture", "Yet another loose thought here")
        self.ok("capture", "A second loose thought, distinct")
        self.refused("capture", "A third loose thought, also distinct")
        other = "11111111-2222-3333-4444-555555555555"
        env = {"CLAUDE_CODE_SESSION_ID": other}
        ledgerio.append(self.cfg, other, {"ev": "prompt", "pid": "p1", "part": 0, "text": "hi"})
        self.assertIn("limit 3", self.ok_env(env, "cap", "show"))

    def test_raise_is_refused_in_agent_context(self) -> None:
        out = self.refused(
            "cap", "raise", "--by", "2", "--evidence", QUOTE, env={"CLAUDE_CODE_AGENT_ID": "a1"}
        )
        self.assertIn("agent context", out)

    def ok_env(self, env: dict[str, str], *argv: str) -> str:
        code, out = self.juno(*argv, env=env)
        self.assertEqual(code, 0, out)
        return out



if __name__ == "__main__":
    unittest.main()
