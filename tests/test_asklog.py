import json
import os
import time
import unittest
from typing import Any

from juno import config
from juno.adapters import asklog, prune
from tests.support.hooks import SID, HookCase
from tests.test_arms import put_arm

DAY = 86400


def question(name: str, labels: list[str], multi: bool = False) -> dict[str, Any]:
    return {
        "question": name,
        "multiSelect": multi,
        "options": [{"label": label, "description": "d"} for label in labels],
    }


class AskLog(HookCase):
    def cfg(self) -> config.Config:
        return config.load(dict(self.env))

    def rows(self) -> list[dict[str, Any]]:
        path = asklog.path(self.sb.state)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines()]

    def ask(
        self,
        questions: list[dict[str, Any]],
        answers: dict[str, Any],
        tid: str = "toolu_0123456789",
        extra: dict[str, Any] | None = None,
        **fields: Any,
    ) -> None:
        tool_in = {"questions": questions, **(extra or {})}
        self.hook(
            "pre_tool_use",
            self.payload(
                "PreToolUse",
                tool_name="AskUserQuestion",
                tool_use_id=tid,
                tool_input=tool_in,
                **fields,
            ),
        )
        self.hook(
            "post_tool_use",
            self.payload(
                "PostToolUse",
                tool_name="AskUserQuestion",
                tool_use_id=tid,
                tool_input={"questions": questions},
                tool_response={"answers": answers},
                **fields,
            ),
        )

    def test_single_pick_of_a_recommended_option_not_first(self) -> None:
        q = question("Which?", ["A", "B (Recommended)", "C"])
        self.ask([q], {"Which?": "B (Recommended)"})
        (row,) = self.rows()
        self.assertEqual(
            {k: v for k, v in row.items() if k != "t"},
            {
                "sid": SID,
                "tid8": "toolu_01",
                "q": 0,
                "n_options": 3,
                "rec": 1,
                "picked": [1],
                "other": False,
                "multi": False,
            },
        )

    def test_a_running_arm_is_logged_on_the_row(self) -> None:
        put_arm(self.sb.state, "demo")
        q = question("Which?", ["A (Recommended)", "B"])
        self.ask([q], {"Which?": "A (Recommended)"})
        (row,) = self.rows()
        self.assertEqual(row["arms"], {"demo": "on"})

    def test_no_recommended_label_gives_minus_one(self) -> None:
        self.ask([question("Which?", ["A", "B"])], {"Which?": "A"})
        self.assertEqual((self.rows()[0]["rec"], self.rows()[0]["picked"]), (-1, [0]))

    def test_free_text_is_other_and_carries_no_text(self) -> None:
        self.ask([question("Which?", ["A", "B"])], {"Which?": "something secret typed"})
        (row,) = self.rows()
        self.assertEqual((row["picked"], row["other"]), ([], True))
        self.assertNotIn("secret", json.dumps(row))

    def test_multi_select_with_a_comma_label_and_other(self) -> None:
        q = question("Pick", ["A, B", "C", "D"], multi=True)
        self.ask([q], {"Pick": "A, B, D, extra"})
        (row,) = self.rows()
        self.assertEqual((row["picked"], row["other"], row["multi"]), ([0, 2], True, True))

    def test_one_row_per_answered_question(self) -> None:
        qs = [question("One?", ["A", "B"]), question("Two?", ["C", "D"]), question("Three?", ["E"])]
        self.ask(qs, {"One?": "B", "Three?": "E"})
        self.assertEqual([(r["q"], r["picked"]) for r in self.rows()], [(0, [1]), (2, [0])])

    def test_prefilled_ask_writes_nothing(self) -> None:
        q = question("Which?", ["A", "B"])
        self.ask([q], {"Which?": "A"}, extra={"answers": {"Which?": "A"}})
        self.assertEqual(self.rows(), [])

    def test_subagent_ask_writes_nothing(self) -> None:
        self.ask([question("Which?", ["A", "B"])], {"Which?": "A"}, agent_id="a1")
        self.assertEqual(self.rows(), [])

    def test_the_ledger_answer_is_still_written(self) -> None:
        self.ask([question("Which?", ["A", "B"])], {"Which?": "B"})
        self.assertEqual([e["text"] for e in self.ledger() if e["ev"] == "answer"], ["B"])


class AskLogRetention(HookCase):
    def cfg(self) -> config.Config:
        return config.load(dict(self.env))

    def test_prune_drops_rows_older_than_asks_days(self) -> None:
        now = time.time()
        os.makedirs(self.sb.state, exist_ok=True)
        old = {"t": now - 400 * DAY, "q": 0}
        new = {"t": now - DAY, "q": 1}
        with open(asklog.path(self.sb.state), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(old) + "\n" + json.dumps(new) + "\nnot json\n")
        prune.prune_state(self.cfg(), now)
        with open(asklog.path(self.sb.state), encoding="utf-8") as handle:
            kept = [json.loads(line) for line in handle.read().splitlines()]
        self.assertEqual([r["q"] for r in kept], [1])

    def test_prune_without_a_log_is_a_noop(self) -> None:
        prune.prune_state(self.cfg())
        self.assertFalse(os.path.exists(asklog.path(self.sb.state)))


if __name__ == "__main__":
    unittest.main()
