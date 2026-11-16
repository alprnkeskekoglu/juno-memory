from tests.e2e.test_phase2_hooks import RULE, Case
from tests.support import e2e


@e2e
class TopicPrecisionOnSearchQueries(Case):
    def test_quoted_search_text_injects_nothing_but_the_specific_trigger_does(self) -> None:
        rule = self.token("PRECISE")
        self.write(
            "rules/topics/ops.md",
            "# Rules: topic:ops\ntriggers: zebrakube, zebrakube-system\n" + RULE.format(rule),
        )
        quiet = self.go(
            'With the Bash tool run exactly: juno search "zebrakube namespace" . '
            "Then reply with the single word: done",
            cwd=self.work,
            allow=["Bash"],
        )
        self.assertEqual(self.contexts(quiet, "PreToolUse", rule), [])
        loud = self.go(
            "Mention zebrakube-system once and reply with the single word: ok", cwd=self.work
        )
        self.assertIn(rule, "\n".join(self.contexts(loud, "UserPromptSubmit", rule)))


@e2e
class TurkishSuffixTrigger(Case):
    def test_suffixed_word_matches_its_trigger_under_the_tr_preset(self) -> None:
        rule = self.token("TURKISH")
        self.write(
            "rules/topics/tracker.md",
            "# Rules: topic:tracker\ntriggers: zebratracker\n" + RULE.format(rule),
        )
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write('\n[language]\npreset = "tr"\n')
        run = self.go("zebratrackerin durumuna bak, sadece tamam yaz", cwd=self.work)
        self.assertIn(rule, "\n".join(self.contexts(run, "UserPromptSubmit", rule)))
