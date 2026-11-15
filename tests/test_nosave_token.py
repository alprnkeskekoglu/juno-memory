import contextlib
import io
import unittest

from juno import cli
from juno.core import prompts
from tests.support.hooks import SID, HookCase

SETS = (
    "[nosave]",
    "  \n[nosave]\n\n",
    "[nosave] keep this out",
    "keep this out [nosave]",
    "keep this out [nosave].",
    "keep this out ([nosave])",
    "keep this out [NoSave]",
    "keep this out\n[nosave]\n",
    "**[nosave]** do the work",
    "pasted `[nosave]` aside then [nosave]",
)
MENTIONS = (
    "the step is skipped on [nosave] in this segment",
    "[nosave]s are ignored",
    "x[nosave] is not the token",
    "see [nosave] docs for details",
)
QUOTED = (
    "the flag is `[nosave]` here",
    "the flag is ``[nosave]`` here",
    "```\n[nosave]\n```",
    "run this:\n~~~sh\necho hi\n[nosave]\n~~~\nthanks",
    "`[nosave]` x",
    'look <pasted_content id="a1">\nskipped on [nosave]\n</pasted_content> thanks',
    "[Pasted text #1 +3 lines] and then some words",
    '<pasted_content id="a1">[nosave]</pasted_content> check it',
    'check it <pasted_content id="a1">[nosave]</pasted_content>',
)


class Rule(unittest.TestCase):
    def test_token_as_first_or_last_word_sets_the_flag(self) -> None:
        for text in SETS:
            with self.subTest(text):
                self.assertTrue(prompts.nosave_set(text))

    def test_mention_inside_a_sentence_does_not_set_it(self) -> None:
        for text in MENTIONS:
            with self.subTest(text):
                self.assertFalse(prompts.nosave_set(text))
                self.assertEqual(prompts.nosave_mentioned(text), "[nosave]" in text.casefold())

    def test_code_spans_blocks_and_pastes_do_not_set_it(self) -> None:
        for text in QUOTED:
            with self.subTest(text):
                self.assertFalse(prompts.nosave_set(text))
                self.assertFalse(prompts.nosave_mentioned(text))

    def test_token_typed_next_to_a_paste_still_sets_it(self) -> None:
        text = 'look <pasted_content id="a">skipped on [nosave]</pasted_content> now [nosave]'
        self.assertTrue(prompts.nosave_set(text))

    def test_empty_and_plain_text(self) -> None:
        for text in ("", "   ", "hello"):
            self.assertFalse(prompts.nosave_set(text))
            self.assertFalse(prompts.nosave_mentioned(text))


class Hook(HookCase):
    def prompt(self, text: str, pid: str = "p1") -> str:
        out = self.hook(
            "user_prompt_submit", self.payload("UserPromptSubmit", prompt=text, prompt_id=pid)
        )
        return self.context(out)

    def flag(self) -> bool:
        return any(e.get("nosave") for e in self.ledger() if e["ev"] == "prompt")

    def refused(self) -> bool:
        env = dict(self.env, CLAUDE_CODE_SESSION_ID=SID, CLAUDE_PID="1")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cli.main(["compact"], env)
        return "[nosave] is set in this segment" in buffer.getvalue()

    def test_rows_of_the_table(self) -> None:
        cases = {
            "alone": ("[nosave]", True),
            "start": ("[nosave] fix the bug", True),
            "end": ("fix the bug [nosave]", True),
            "sentence": ("a feature that is skipped on [nosave] runs", False),
            "inline code": ("the flag `[nosave]` is documented", False),
            "fenced": ("example:\n```\nskipped on [nosave]\n```\nok", False),
            "paste": ('see <pasted_content id="a">skipped on [nosave]</pasted_content> ok', False),
        }
        for name, (text, want) in cases.items():
            with self.subTest(name):
                self.setUp()
                self.prompt(text)
                self.assertEqual(self.flag(), want)
                self.assertEqual(self.refused(), want)

    def test_set_notice_comes_once_in_the_setting_turn(self) -> None:
        first = self.prompt("fix the bug [nosave]", "p1")
        self.assertEqual(first.count("[nosave] is set for this segment"), 1)
        self.assertIn("nothing is written to the vault", first)
        later = self.prompt("go on [nosave]", "p2")
        self.assertNotIn("is set for this segment", later)
        self.assertTrue(self.flag())

    def test_flag_is_sticky_for_later_prompts(self) -> None:
        self.prompt("[nosave] first")
        self.prompt("a plain later prompt", "p2")
        self.assertTrue(self.refused())

    def test_mention_notice_only_for_a_typed_mention(self) -> None:
        said = self.prompt("it is skipped on [nosave] in this case")
        self.assertIn("[nosave] was mentioned but not set", said)
        self.assertNotIn("is set for this segment", said)
        for text in ("hello", "the flag `[nosave]` here", "```\n[nosave]\n```"):
            with self.subTest(text):
                self.assertNotIn("was mentioned", self.prompt(text, "p9"))
        self.assertFalse(self.flag())

    def test_notice_survives_a_full_budget(self) -> None:
        self.set_config("[caps]\nhook_output_chars = 400\n")
        self.write("tasks/ABC-100.md", "---\nstatus: open\n---\n" + "line\n" * 300)
        text = self.prompt("work on ABC-100 [nosave]")
        self.assertIn("[nosave] is set for this segment", text)
        self.assertLessEqual(len(text), 400)


if __name__ == "__main__":
    unittest.main()
