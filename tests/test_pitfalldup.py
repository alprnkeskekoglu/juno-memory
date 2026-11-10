import itertools
import unittest

from juno.core import pitfalldup, topics

# Texts shaped like the two real duplicate pairs from issue #8 (the originals were already
# cleaned out of the vault, so the wording is reconstructed from the issue description).
CLAUDE_ERR = (
    "Input must be provided either through stdin or as a prompt argument when using --print"
)
CLAUDE_A = (
    "claude -p with a variadic --disallowedTools before the prompt swallows the prompt; "
    "pass the prompt first or end the list with --."
)
CLAUDE_B = (
    "Running claude -p with --disallowedTools placed before the prompt eats the prompt "
    "argument; put the prompt before the variadic flag or use --disallowedTools=X."
)
ZSH_A = "zsh expands an unquoted == in 'echo ====' as an equals expansion; quote the separator or use printf."
ZSH_B = (
    "An unquoted echo == separator in zsh fails with equals expansion; quote the separator "
    "or use printf instead."
)
ZSH_ERR = "(eval):1: == not found"

# The five distinct lines that were in the real pitfalls.md next to the pairs.
REAL_OTHERS = [
    "A grep for a function in a guessed path (juno/core/env.py) failed; locate it repo-wide "
    "first, it lives in juno/adapters/env.py.",
    "git worktree remove --force failed on a locked agent worktree; check the lock reason and "
    "use 'remove -f -f' or unlock first before deleting the branch.",
    "A commit was made while the full test suite still had 15 failing tests because the result "
    "was misread; read the full-suite summary before committing. Tests must pin their own slot "
    "times, not read config.toml.",
    "A test hardcoded the cap value 30 and failed after the default changed; grep for the "
    "literal and tie tests to writegate.RUN_MAX before running the suite.",
    "Vault writes stopped at the per-segment cap of 50; check juno cap show early in long "
    "research sessions and raise it with the user's approval before batching writes.",
]


def line(text: str, triggers: str = "x1", evidence: str = "ev one", day: str = "2026-11-01") -> str:
    return f"- {text} · triggers: {triggers} · {evidence}; segment abcd1234 · {day}"


class Near(unittest.TestCase):
    def test_paraphrase_with_shared_words_is_near(self) -> None:
        self.assertTrue(pitfalldup.near_text(ZSH_A, ZSH_B))

    def test_same_error_evidence_is_a_signal(self) -> None:
        self.assertFalse(pitfalldup.near_text(CLAUDE_A, ZSH_A))
        self.assertTrue(pitfalldup.same_error(f"{CLAUDE_ERR}; foo", f"x; {CLAUDE_ERR}"))
        self.assertTrue(pitfalldup.same_error(ZSH_ERR, f"zsh said {ZSH_ERR}"))

    def test_short_or_unrelated_evidence_is_not_error_evidence(self) -> None:
        self.assertFalse(pitfalldup.same_error("src/money.py", "src/money.py"))
        self.assertFalse(pitfalldup.same_error("Ran 1047 tests OK", "Ran 1047 tests OK"))
        self.assertFalse(pitfalldup.same_error(ZSH_ERR, CLAUDE_ERR))

    def test_distinct_real_pitfalls_stay_separate(self) -> None:
        for a, b in itertools.combinations(REAL_OTHERS + [CLAUDE_A, ZSH_A], 2):
            self.assertFalse(pitfalldup.near_text(a, b), (a, b))

    def test_find_by_text_or_evidence(self) -> None:
        lines = ["# Pitfalls", "", line(REAL_OTHERS[0]), line(ZSH_A, evidence=ZSH_ERR)]
        self.assertEqual(pitfalldup.find(lines, ZSH_B, "other ev"), 3)
        self.assertEqual(pitfalldup.find(lines, "something else entirely new", ZSH_ERR), 3)
        self.assertIsNone(pitfalldup.find(lines, "something else entirely new", "other ev"))


class Merge(unittest.TestCase):
    def test_longer_text_wins_triggers_union_date_and_segment_updated(self) -> None:
        old = line(ZSH_A, "zsh, echo", ZSH_ERR)
        new = pitfalldup.merge(old, ZSH_B, ["echo", "printf"], "other ev", "ffff0000", "2026-11-11")
        found = topics.pitfall_line(new)
        assert found is not None
        self.assertEqual(found["text"], ZSH_B)
        self.assertEqual(found["triggers"], ["zsh", "echo", "printf"])
        self.assertEqual(found["date"], "2026-11-11")
        self.assertIn("segment ffff0000", new)
        self.assertIn("other ev", new)

    def test_shorter_new_text_keeps_the_old_text_and_evidence(self) -> None:
        old = line(ZSH_B, "zsh", ZSH_ERR)
        new = pitfalldup.merge(old, ZSH_A[:60], ["printf"], "other ev", "ffff0000", "2026-11-11")
        found = topics.pitfall_line(new)
        assert found is not None
        self.assertEqual(found["text"], ZSH_B)
        self.assertEqual(found["triggers"], ["zsh", "printf"])
        self.assertIn(ZSH_ERR, new)
        self.assertNotIn("other ev", new)

    def test_dash_triggers_and_cap(self) -> None:
        old = line(ZSH_A, "-", ZSH_ERR)
        new = pitfalldup.merge(old, ZSH_A, [f"t{i}" for i in range(30)], ZSH_ERR, "ffff0000", "d")
        found = topics.pitfall_line(new)
        assert found is not None
        self.assertEqual(len(found["triggers"]), topics.MAX_TRIGGERS)


class Pairs(unittest.TestCase):
    def test_pairs_use_file_line_numbers(self) -> None:
        text = "\n".join(
            [
                "# Pitfalls",
                "",
                line(REAL_OTHERS[0]),
                line(ZSH_A, evidence=ZSH_ERR),
                line(REAL_OTHERS[1]),
                line(CLAUDE_A, evidence=CLAUDE_ERR),
                line(ZSH_B, evidence="other"),
                line(CLAUDE_B, evidence=CLAUDE_ERR),
            ]
        )
        self.assertEqual(pitfalldup.pairs(text), [(4, 7), (6, 8)])

    def test_real_distinct_lines_have_no_pairs(self) -> None:
        text = "\n".join(line(t, evidence=f"evidence number {i}") for i, t in enumerate(REAL_OTHERS))
        self.assertEqual(pitfalldup.pairs(text), [])


if __name__ == "__main__":
    unittest.main()
