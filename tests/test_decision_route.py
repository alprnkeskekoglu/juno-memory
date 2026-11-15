import os
import re

from juno.adapters import writelog
from tests.support.write import WriteCase

QUOTE = "this is the first typed prompt"
LINE = re.compile(r"^- (.+) \(user pid00001, \d{4}-\d{2}-\d{2}\)$", re.M)


class Route(WriteCase):
    JIRA = '[jira]\nprefixes = ["ABC"]\nbase_url = "https://jira.example.test"'

    def setUp(self) -> None:
        super().setUp()
        self.set_config(self.JIRA)

    def ok(self, *argv: str) -> str:
        code, out = self.juno(*argv)
        self.assertEqual(code, 0, out)
        return out

    def refused(self, *argv: str) -> str:
        code, out = self.juno(*argv)
        self.assertEqual(code, 2, out)
        self.assertTrue(out.startswith("Juno: refused:"), out)
        return out

    def decide(self, topic: str, text: str, *extra: str) -> str:
        return self.ok("decision", "--topic", topic, "--evidence", QUOTE, text, *extra)

    def wid(self, out: str) -> str:
        return out.split("(write ")[1].split(";")[0]

    def live(self) -> int:
        return writelog.live_count(writelog.read(self.cfg), self.sid)

    def exists(self, rel: str) -> bool:
        return os.path.exists(self.vp(rel))

    def profile(self, name: str = "app", extra: str = "") -> None:
        self.put(
            f"projects/{name}.md",
            f"---\nrepo: {name}\nupdated: 2026-01-01\n{extra}---\n\n## Purpose\n\n- Serves invoices\n",
        )


class Owners(Route):
    def test_visible_profile_gets_a_first_decisions_section(self) -> None:
        self.profile()
        out = self.decide("app", "Use Postgres for storage")
        self.assertIn("projects/app.md", out)
        text = self.get("projects/app.md")
        self.assertEqual(text.count("## "), 2)
        self.assertLess(text.index("## Decisions"), text.index("## Purpose"))
        self.assertIn("## Decisions\n\n- Use Postgres for storage (user pid00001, ", text)
        self.assertNotIn("updated: 2026-01-01", text)
        self.assertFalse(self.exists("decisions/app.md"))

    def test_hidden_profile_is_not_an_owner(self) -> None:
        self.profile(extra="visibility: private\n")
        out = self.decide("app", "Use Postgres for storage")
        self.assertIn("decisions/app.md", out)
        self.assertNotIn("Decisions", self.get("projects/app.md"))

    def test_repo_named_like_a_ticket_goes_to_its_profile(self) -> None:
        self.profile("app-2")
        self.decide("app-2", "Use Postgres for storage")
        self.assertIn("Use Postgres", self.get("projects/app-2.md"))
        self.assertFalse(self.exists("decisions/APP-2.md"))
        self.assertFalse(self.exists("tasks/APP-2.md"))

    def test_ticket_without_a_task_file_creates_it_in_one_write(self) -> None:
        before = self.live()
        out = self.decide("abc-1", "Use queues for retries")
        self.assertIn("tasks/ABC-1.md", out)
        text = self.get("tasks/ABC-1.md")
        self.assertIn("jira: ABC-1", text)
        self.assertIn("url: https://jira.example.test/browse/ABC-1", text)
        self.assertNotIn("title:", text)
        self.assertNotIn("status:", text)
        self.assertIn("## Decisions\n\n- Use queues for retries (user pid00001, ", text)
        self.assertIn("## Notes", text)
        self.assertEqual(self.live(), before + 1)
        self.assertFalse(self.exists("decisions/ABC-1.md"))
        self.ok("undo", self.wid(out))
        self.assertFalse(self.exists("tasks/ABC-1.md"))

    def test_existing_task_file_keeps_its_notes(self) -> None:
        self.put(
            "tasks/ABC-2.md",
            "---\njira: ABC-2\ntitle: Export\nupdated: 2026-01-01\n---\n\n"
            "## Decisions\n\n## Notes\n\n- keep this note\n",
        )
        self.decide("ABC-2", "Use queues for retries")
        text = self.get("tasks/ABC-2.md")
        self.assertIn("## Decisions\n\n- Use queues for retries", text)
        self.assertTrue(text.endswith("## Notes\n\n- keep this note\n"))
        self.assertIn("title: Export", text)
        self.assertNotIn("updated: 2026-01-01", text)

    def test_plain_topic_is_unchanged(self) -> None:
        out = self.decide("storage", "Use Postgres for storage")
        self.assertIn("decisions/storage.md", out)
        text = self.get("decisions/storage.md")
        self.assertIn("## Current\n\n- Use Postgres for storage (user pid00001, ", text)
        self.assertNotIn("## History", text)

    def test_evidence_must_be_the_users_words(self) -> None:
        self.profile()
        self.refused("decision", "--topic", "app", "--evidence", "repo:app", "Use Postgres here")
        self.assertNotIn("Decisions", self.get("projects/app.md"))


class NoPrefixes(Route):
    JIRA = "[jira]\nprefixes = []"

    def test_ticket_shaped_topic_without_prefixes_stays_a_topic(self) -> None:
        out = self.decide("zzz-9", "Use queues for retries")
        self.assertIn("decisions/ZZZ-9.md", out)
        self.assertFalse(self.exists("tasks/ZZZ-9.md"))


class Supersede(Route):
    def test_old_line_moves_to_the_archive_as_one_undoable_write(self) -> None:
        self.profile()
        self.decide("app", "Use Postgres for storage")
        before = self.get("projects/app.md")
        count = self.live()
        out = self.decide("app", "Use DynamoDB for storage", "--supersedes", "1")
        self.assertEqual(self.live(), count + 1)
        text = self.get("projects/app.md")
        self.assertIn("DynamoDB", text)
        self.assertNotIn("Postgres", text)
        wid = self.wid(out)
        archive = self.get("archive/decisions/app.md")
        self.assertRegex(
            archive,
            r"- Use Postgres for storage \(user pid00001, [\d-]+\)"
            rf" · superseded [\d-]+ · from projects/app\.md · {wid}",
        )
        self.ok("undo", wid)
        self.assertEqual(self.get("projects/app.md"), before)
        self.assertFalse(self.exists("archive/decisions/app.md"))

    def test_topic_file_writes_no_history(self) -> None:
        self.decide("storage", "Use Postgres for storage")
        before = self.get("decisions/storage.md")
        out = self.decide("storage", "Use DynamoDB for storage", "--supersedes", "1")
        text = self.get("decisions/storage.md")
        self.assertNotIn("History", text)
        self.assertNotIn("Postgres", text)
        self.assertIn("Postgres", self.get("archive/decisions/storage.md"))
        self.ok("undo", self.wid(out))
        self.assertEqual(self.get("decisions/storage.md"), before)

    def test_an_existing_history_is_left_alone(self) -> None:
        self.put(
            "decisions/old.md",
            "---\ntopic: old\nupdated: 2026-01-01\n---\n\n## Current\n\n- Use A (user pid00001, "
            "2026-01-01)\n\n## History\n\n- Use Z (user pid00001, 2025-01-01) · superseded 2026-01-01\n",
        )
        self.decide("old", "Use B instead", "--supersedes", "1")
        text = self.get("decisions/old.md")
        self.assertEqual(text.count("Use Z"), 1)
        self.assertNotIn("Use A", text)
        self.assertEqual(len(LINE.findall(text.split("## History")[1])), 0)

    def test_zero_and_out_of_range_are_refused(self) -> None:
        self.decide("storage", "Use Postgres for storage")
        before = self.get("decisions/storage.md")
        for number in ("0", "2", "-1"):
            out = self.refused(
                "decision", "--topic", "storage", "--evidence", QUOTE, "Use X here",
                "--supersedes", number,
            )  # fmt: skip
            self.assertIn("--supersedes", out)
        self.assertEqual(self.get("decisions/storage.md"), before)

    def test_refused_while_a_legacy_file_exists(self) -> None:
        self.profile()
        self.decide("app", "Use Postgres for storage")
        self.put(
            "decisions/app.md",
            "---\ntopic: app\n---\n\n## Current\n\n- Use X (user pid00001, 2026-01-01)\n",
        )
        before = self.get("projects/app.md")
        out = self.refused(
            "decision", "--topic", "app", "--evidence", QUOTE, "Use Y here", "--supersedes", "1"
        )
        self.assertIn("juno migrate --decisions", out)
        self.assertEqual(self.get("projects/app.md"), before)
        self.decide("app", "Use Y here")
        self.assertIn("Use Y here", self.get("projects/app.md"))
