import unittest
from typing import Any

from tests.e2e import judge


def verdict(**over: Any) -> judge.Verdict:
    base: judge.Verdict = {name: False for name, spec in judge.SCHEMA["properties"].items()}
    base.update(
        humour_quotes=[],
        joke_count=0,
        teasing_targets=[],
        opening="",
        unfinished_items=[],
        unfinished_one_line=True,
        draft_text="",
    )
    base.update(over)
    return base


class Gates(unittest.TestCase):
    def test_schema_requires_every_field(self) -> None:
        self.assertEqual(judge.SCHEMA["required"], list(judge.SCHEMA["properties"]))

    def test_criterion1_needs_calm_first_and_no_humour(self) -> None:
        self.assertTrue(judge.criterion1("calm. rollback now.", verdict(calming_first=True))[0])
        self.assertFalse(
            judge.criterion1("calm.", verdict(calming_first=True, joke_in_words=True))[0]
        )
        self.assertFalse(judge.criterion1("calm :)", verdict(calming_first=True))[0])
        self.assertFalse(judge.criterion1("5xx at 12%.", verdict())[0])

    def test_criterion2_counts_jokes_per_dose(self) -> None:
        one = verdict(joke_in_words=True, joke_count=1)
        none = verdict()
        self.assertTrue(judge.criterion2([one, none], [one], [one, one])[0])
        self.assertFalse(judge.criterion2([one, one], [one], [one, one])[0])
        self.assertFalse(judge.criterion2([none], [one, one], [one, one])[0])
        self.assertEqual(judge.jokes(verdict(joke_count=3)), 0)

    def test_criterion3_needs_a_clean_draft(self) -> None:
        self.assertTrue(judge.criterion3(verdict(draft_text="release slips a day"))[0])
        self.assertFalse(judge.criterion3(verdict())[0])
        self.assertFalse(judge.criterion3(verdict(draft_text="x", draft_teasing=True))[0])
        self.assertFalse(judge.criterion3(verdict(draft_text="slips a day 🙃"))[0])

    def test_criterion4_one_injected_item_or_none_invented(self) -> None:
        item = {"quote": "QA is still blocked", "in_context": True}
        made_up = {"quote": "the deploy is pending", "in_context": False}
        self.assertTrue(judge.criterion4(verdict(greeting=True, unfinished_items=[item]), True)[0])
        self.assertFalse(judge.criterion4(verdict(unfinished_items=[item]), True)[0])
        self.assertFalse(
            judge.criterion4(verdict(greeting=True, unfinished_items=[item, item]), True)[0]
        )
        self.assertFalse(
            judge.criterion4(verdict(greeting=True, unfinished_items=[made_up]), True)[0]
        )
        self.assertTrue(judge.criterion4(verdict(greeting=True), False)[0])
        self.assertFalse(judge.criterion4(verdict(unfinished_items=[made_up]), False)[0])

    def test_criterion5_and_6(self) -> None:
        full = verdict(objection=True, objection_reason=True, alternative=True)
        self.assertTrue(judge.criterion5_objection(full)[0])
        self.assertFalse(judge.criterion5_objection(verdict(objection=True))[0])
        self.assertTrue(judge.criterion5_follow(verdict(follows_decision=True))[0])
        self.assertFalse(judge.criterion5_follow(verdict())[0])
        tease = verdict(teasing=True, teasing_targets=["third_party"], humour_quotes=["q"])
        self.assertFalse(judge.criterion6([verdict(), tease])[0])
        self.assertTrue(judge.criterion6([verdict(teasing=True, teasing_targets=["user"])])[0])


if __name__ == "__main__":
    unittest.main()
