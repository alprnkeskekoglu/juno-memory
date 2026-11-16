"""Issue #36 part C: recall (suffix folding, trigger candidates for knowledge without triggers)."""

import contextlib
import io
import unittest
from typing import Any

from juno import cli
from juno.adapters import queues, usageio
from juno.core import fold, topics, triggerprop
from tests.support import PYTHON
from tests.support.hooks import SID
from tests.test_extract import ExtractCase

ENTRIES: list[Any] = [
    ["topic-a", "rules/topics/a.md", ["tracker"]],
    ["topic-b", "rules/topics/b.md", ["deploy"]],
    ["topic-c", "rules/topics/c.md", ["kural"]],
]


def names(text: str, preset: str) -> list[str]:
    return [e[0] for e in topics.matches(ENTRIES, text, preset)]


class Suffixes(unittest.TestCase):
    def test_criterion_7_turkish_forms_match_under_the_tr_preset(self) -> None:
        self.assertEqual(names("trackerin durumu", "tr"), ["topic-a"])
        self.assertEqual(names("trackerte hata var", "tr"), ["topic-a"])
        self.assertEqual(names("deploy'u kontrol et", "tr"), ["topic-b"])
        self.assertEqual(names("Deploy’u kontrol et", "tr"), ["topic-b"])
        self.assertEqual(names("kurallari oku", "tr"), ["topic-c"])
        self.assertEqual(names("KURALLARI oku", "tr"), ["topic-c"])
        self.assertEqual(names("kuralların listesi", "tr"), ["topic-c"])

    def test_criterion_7_without_a_preset_nothing_changes(self) -> None:
        for text in ("trackerin durumu", "trackerte hata var", "kurallari oku"):
            self.assertEqual(names(text, "none"), [], text)
        self.assertEqual(names("deploy'u kontrol et", "none"), ["topic-b"])  # the quote is a break

    def test_unrelated_words_do_not_match(self) -> None:
        self.assertEqual(names("trackerler mantar", "tr"), ["topic-a"])
        self.assertEqual(names("kuralsiz mantik deployment", "tr"), [])

    def test_stems(self) -> None:
        self.assertIn("tracker", fold.stems("trackerin", "tr"))
        self.assertEqual(fold.stems("trackerin", "none"), [])
        self.assertEqual(fold.stems("ab", "tr"), [])


class Proposal(unittest.TestCase):
    def test_triggers_come_from_title_and_body(self) -> None:
        got = triggerprop.propose(
            "Retry queue facts",
            "Messages wait in the retry-queue for 30 minutes.\nThe retry-queue drains at night.\n",
            "none",
        )
        self.assertIn("retry-queue", got)
        self.assertLessEqual(len(got), triggerprop.MAX_PROPOSED)
        for trigger in got:
            self.assertIsNone(topics.trigger_problem(trigger))
        self.assertNotIn("that", got)

    def test_nothing_to_propose_from_an_empty_body(self) -> None:
        self.assertEqual(triggerprop.propose("", "", "none"), [])

    def test_user_language_form_needs_a_preset(self) -> None:
        said = "kuyruk mesajlari nasil bekler"
        item = "Messages wait in the retry queue"
        self.assertEqual(triggerprop.user_forms(said, item, [], "none", []), [])
        got = triggerprop.user_forms(said, item, [], "tr", ["nasil"])
        self.assertIn("kuyruk", got)
        self.assertNotIn("nasil", got)
        self.assertLessEqual(len(got), triggerprop.MAX_USER_FORMS)


KNOWLEDGE = (
    "---\ntitle: Retry queue facts\nsources: [x]\n---\n"
    "Messages wait in the retry-queue for 30 minutes before the broker drops them.\n"
)


class TriggerCandidates(ExtractCase):
    def juno(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        env = dict(self.env, CLAUDE_CODE_SESSION_ID=SID, JUNO_PYTHON=PYTHON)
        with contextlib.redirect_stdout(buffer):
            code = cli.main(list(argv), env)
        return code, buffer.getvalue()

    def read_by_hand(self, rel: str = "knowledge/queue.md") -> None:
        usageio.append(self.cfg().state, SID, [rel], "read")

    def pending(self) -> list[dict[str, Any]]:
        return queues.pending(self.cfg(), queues.RULES)

    def test_criterion_8_candidate_after_a_read_and_applied_only_on_confirmation(self) -> None:
        self.write("knowledge/queue.md", KNOWLEDGE)
        self.start()
        self.read_by_hand()
        self.ext()
        (item,) = self.pending()
        self.assertEqual(item["kind"], "triggers")
        self.assertEqual(item["file"], "knowledge/queue.md")
        self.assertIn("retry-queue", item["triggers"])
        self.assertEqual(self.read("knowledge/queue.md"), KNOWLEDGE)  # nothing written yet

        code, out = self.juno("rule", "accept", item["id"])
        self.assertEqual(code, 2, out)  # the user has not chosen it
        self.assertEqual(self.read("knowledge/queue.md"), KNOWLEDGE)

        self.prompt(f"yes {item['id']}")
        code, out = self.juno("rule", "accept", item["id"])
        self.assertEqual(code, 0, out)
        text = self.read("knowledge/queue.md")
        self.assertIn("triggers: [", text)
        self.assertIn("retry-queue", text)
        self.assertEqual(self.pending(), [])
        self.assertTrue(topics.build_entries([("knowledge/queue.md", text)]))

    def test_session_end_candidate_drops_dates_and_ticket_ids(self) -> None:
        self.write(
            "knowledge/queue.md",
            KNOWLEDGE + "Seen on 2026-11-15 in ABC-123456, 2026-11-15 and ABC-123456 again.\n",
        )
        self.start()
        self.read_by_hand()
        self.ext()
        (item,) = self.pending()
        self.assertIn("retry-queue", item["triggers"])
        for token in ("2026-11-15", "abc-123456"):
            self.assertNotIn(token, item["triggers"])

    def test_rejected_candidate_writes_nothing_and_is_not_proposed_again(self) -> None:
        self.write("knowledge/queue.md", KNOWLEDGE)
        self.start()
        self.read_by_hand()
        self.ext()
        (item,) = self.pending()
        self.assertEqual(self.juno("rule", "reject", item["id"])[0], 0)
        self.assertEqual(self.read("knowledge/queue.md"), KNOWLEDGE)
        self.redirty()
        self.ext()
        self.assertEqual(self.pending(), [])

    def test_no_candidate_without_a_read_or_when_triggers_exist(self) -> None:
        self.write("knowledge/queue.md", KNOWLEDGE)
        self.write(
            "knowledge/other.md",
            "---\ntitle: Other\nsources: [x]\ntriggers: [other-thing]\n---\nbody text here\n",
        )
        self.start()
        self.ext()
        self.assertEqual(self.pending(), [])
        self.redirty()
        self.read_by_hand("knowledge/other.md")
        self.ext()
        self.assertEqual(self.pending(), [])

    def test_candidate_shows_in_the_nudge_and_in_pending(self) -> None:
        self.write("knowledge/queue.md", KNOWLEDGE)
        self.start()
        self.read_by_hand()
        self.ext()
        out = self.juno("rule", "pending")[1]
        self.assertIn("knowledge/queue.md", out)
        self.assertIn("triggers", out)
        from juno.core import candidates

        nudge = candidates.rules_nudge(self.pending())
        self.assertIn("knowledge/queue.md", nudge)
        self.assertNotIn("user said", nudge)

    def test_user_language_form_is_proposed_for_a_read_item(self) -> None:
        self.set_config('[language]\npreset = "tr"\n')
        self.write(
            "knowledge/queue.md",
            KNOWLEDGE.replace(
                "title: Retry queue facts", "title: Retry queue facts\ntriggers: [retry-queue]"
            ),
        )
        self.start("kuyruk mesajlari ne kadar bekler")
        self.read_by_hand()
        self.ext()
        (item,) = self.pending()
        self.assertIn("kuyruk", item["triggers"])


if __name__ == "__main__":
    unittest.main()
