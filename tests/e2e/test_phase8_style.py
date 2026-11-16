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

    def test_normal_style_answers_juno(self) -> None:
        answer = self.ask({"outputStyle": STYLE_CHOICES["2"]})
        self.assertRegex(answer, CLAIMS_JUNO)

    def test_without_the_style_it_does_not_claim_to_be_juno(self) -> None:
        answer = self.ask({})
        self.assertNotRegex(answer, CLAIMS_JUNO)

    def test_the_bare_plugin_name_selects_nothing(self) -> None:
        answer = self.ask({"outputStyle": "juno"})
        self.assertNotRegex(answer, CLAIMS_JUNO)


JOKE_MARKS = re.compile(r":\)|;\)|:D|[\U0001F300-\U0001FAFF\u2600-\u27BF]")
INCIDENT = (
    "prod is down: 5xx at 12% since the last deploy, customers are writing in. "
    "What do we do right now?"
)
DRAFT = "Draft a two-line message I can send to my team lead that the release slipped by a day."


@e2e
class CharacterHoldsItsRules(unittest.TestCase):
    """Criteria 1 and 3 of issue #35; 2 and 4-6 are read by hand (see docs/e2e.md)."""

    def setUp(self) -> None:
        harness.require_claude(self)
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.work = os.path.join(self.sb.root, "work")
        os.makedirs(self.work)

    def ask(self, dose: str, prompt: str) -> str:
        extra = [
            "--setting-sources=local",
            "--settings",
            json.dumps({"outputStyle": STYLE_CHOICES[dose]}),
            "--no-session-persistence",
        ]
        run = harness.run_claude(
            prompt, harness.claude_env(self.sb, False), cwd=self.work, extra=extra
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.result

    def test_a_production_error_has_no_joke_or_emoji_at_any_dose(self) -> None:
        for dose in STYLE_CHOICES:
            with self.subTest(dose=dose):
                self.assertNotRegex(self.ask(dose, INCIDENT), JOKE_MARKS)

    def test_a_drafted_outward_message_has_no_emoji_at_any_dose(self) -> None:
        for dose in STYLE_CHOICES:
            with self.subTest(dose=dose):
                self.assertNotRegex(self.ask(dose, DRAFT), JOKE_MARKS)


if __name__ == "__main__":
    unittest.main()
