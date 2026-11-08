import json
import os
import re
import unittest

from juno.cmd.init import STYLE_CHOICES
from tests.e2e import harness
from tests.support import Sandbox, e2e

PROMPT = "What is your name? Answer in one short sentence."
CLAIMS_JUNO = re.compile(r"(i'?m|i am|my name is|called|name's)\s+juno", re.IGNORECASE)


@e2e
class OutputStyleGivesTheIdentity(unittest.TestCase):
    def setUp(self) -> None:
        harness.require_claude(self)
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.work = os.path.join(self.sb.root, "work")
        os.makedirs(self.work)

    def ask(self, settings: dict[str, str]) -> str:
        extra = [
            "--setting-sources=local",
            "--settings",
            json.dumps(settings),
            "--no-session-persistence",
        ]
        run = harness.run_claude(
            PROMPT, harness.claude_env(self.sb, False), cwd=self.work, extra=extra
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(run.result.strip(), "empty claude result")
        return run.result

    def test_selected_style_answers_juno(self) -> None:
        answer = self.ask({"outputStyle": STYLE_CHOICES["1"]})
        self.assertRegex(answer, CLAIMS_JUNO)

    def test_witty_style_answers_juno(self) -> None:
        answer = self.ask({"outputStyle": STYLE_CHOICES["2"]})
        self.assertRegex(answer, CLAIMS_JUNO)

    def test_without_the_style_it_does_not_claim_to_be_juno(self) -> None:
        answer = self.ask({})
        self.assertNotRegex(answer, CLAIMS_JUNO)

    def test_the_bare_plugin_name_selects_nothing(self) -> None:
        answer = self.ask({"outputStyle": "juno"})
        self.assertNotRegex(answer, CLAIMS_JUNO)


if __name__ == "__main__":
    unittest.main()
