import unittest
from typing import Any, ClassVar

from juno import paths
from juno.adapters import env
from juno.core import toolrec


class TestInteractiveOverride(unittest.TestCase):
    HOME = "/home/x"

    def env(self, **over: str) -> dict[str, str]:
        base = {
            "HOME": self.HOME,
            "JUNO_TEST_INTERACTIVE": "1",
            "JUNO_CONFIG": "/tmp/t/juno.toml",
            "JUNO_VAULT": "/tmp/t/vault",
            "JUNO_STATE": "/tmp/t/state",
        }
        base.update(over)
        return {k: v for k, v in base.items() if v}

    def test_needs_config_vault_and_state_to_be_non_default(self) -> None:
        self.assertTrue(env.test_override(self.env()))
        defaults = {
            "JUNO_CONFIG": paths.default_config_path({"HOME": self.HOME}),
            "JUNO_VAULT": paths.expand(paths.DEFAULT_VAULT, {"HOME": self.HOME}),
            "JUNO_STATE": paths.default_state_dir({"HOME": self.HOME}),
        }
        for name in defaults:
            self.assertFalse(env.test_override(self.env(**{name: ""})), name + " unset")
            self.assertFalse(env.test_override(self.env(**{name: defaults[name]})), name)

    def test_flag_must_be_one(self) -> None:
        self.assertFalse(env.test_override(self.env(JUNO_TEST_INTERACTIVE="0")))
        self.assertFalse(
            env.is_interactive(self.env(CLAUDE_CODE_SESSION_ATTENDED="0", JUNO_VAULT=""))
        )


class TestAnswerTexts(unittest.TestCase):
    QUESTION: ClassVar[dict[str, Any]] = {
        "question": "Which?",
        "multiSelect": True,
        "options": [
            {"label": "Use tabs", "description": "tabs for indent"},
            {"label": "Spaces", "description": "four spaces"},
        ],
    }

    def chosen(self, answer: object) -> list[str]:
        tool_in = {"questions": [self.QUESTION]}
        return toolrec.chosen_texts(tool_in, {"answers": {"Which?": answer}})

    def test_only_selected_labels_and_free_text_count(self) -> None:
        self.assertEqual(self.chosen("Use tabs, Spaces"), ["Use tabs", "Spaces"])
        self.assertEqual(self.chosen("Use tabs, my own idea"), ["Use tabs", "my own idea"])
        self.assertEqual(self.chosen(["Spaces"]), ["Spaces"])
        for text in self.chosen("Use tabs, Spaces"):
            self.assertNotIn("indent", text)
            self.assertNotIn("four", text)

    def test_single_select_free_text(self) -> None:
        question = dict(self.QUESTION, multiSelect=False)
        out = toolrec.chosen_texts({"questions": [question]}, {"answers": {"Which?": "Spaces"}})
        self.assertEqual(out, ["Spaces"])


if __name__ == "__main__":
    unittest.main()
