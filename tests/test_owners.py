import unittest

from juno.core import owners, packs, taskfile
from juno.core.detect import Tickets
from juno.core.errors import Refused

JIRA = Tickets(prefixes=("APP",))
OFF = Tickets()


class RoutingTests(unittest.TestCase):
    def test_profile_wins_over_ticket_shape(self) -> None:
        got = owners.decision_owner("app-2", {"app-2", "other"}, JIRA)
        self.assertEqual((got.rel, got.kind, got.topic), ("projects/app-2.md", "profile", "app-2"))

    def test_hidden_profile_is_not_in_profiles(self) -> None:
        got = owners.decision_owner("app-2", {"other"}, JIRA)
        self.assertEqual((got.rel, got.kind), ("tasks/APP-2.md", "ticket"))

    def test_profile_match_is_exact(self) -> None:
        got = owners.decision_owner("APP-2", {"app-2"}, JIRA)
        self.assertEqual(got.kind, "ticket")

    def test_configured_prefix_uppercases(self) -> None:
        got = owners.decision_owner("app-7", set(), JIRA)
        self.assertEqual((got.rel, got.topic), ("tasks/APP-7.md", "APP-7"))

    def test_ticket_shape_without_prefixes_keeps_decisions_path(self) -> None:
        got = owners.decision_owner("app-7", set(), OFF)
        self.assertEqual((got.rel, got.kind, got.topic), ("decisions/APP-7.md", "topic", "APP-7"))

    def test_unconfigured_prefix_keeps_decisions_path(self) -> None:
        self.assertEqual(owners.decision_owner("zzz-7", set(), JIRA).rel, "decisions/ZZZ-7.md")

    def test_plain_topic(self) -> None:
        got = owners.decision_owner("caching", set(), JIRA)
        self.assertEqual(
            (got.rel, got.kind, got.topic), ("decisions/caching.md", "topic", "caching")
        )

    def test_invalid_slug_refused(self) -> None:
        with self.assertRaises(Refused):
            owners.decision_owner("Bad Topic", set(), JIRA)


class LegacyTests(unittest.TestCase):
    def test_ticket(self) -> None:
        self.assertEqual(owners.legacy_decision_rels("ticket", "app-2"), ["decisions/APP-2.md"])

    def test_repo_plain(self) -> None:
        self.assertEqual(owners.legacy_decision_rels("repo", "ash"), ["decisions/ash.md"])

    def test_repo_ticket_shaped(self) -> None:
        self.assertEqual(
            owners.legacy_decision_rels("repo", "app-2"),
            ["decisions/app-2.md", "decisions/APP-2.md"],
        )
        self.assertEqual(owners.legacy_decision_rels("repo", "APP-2"), ["decisions/APP-2.md"])


class MergedTests(unittest.TestCase):
    def test_order_and_exact_duplicates(self) -> None:
        got = owners.merged_decisions("- a\n\n- b\n", "- b\n- c\n", "- c\n- a\n- d")
        self.assertEqual(got, ["- a", "- b", "- c", "- d"])

    def test_near_duplicate_kept(self) -> None:
        self.assertEqual(owners.merged_decisions("- a", "- a ", "- A"), ["- a", "- a ", "- A"])

    def test_blank_lines_dropped(self) -> None:
        self.assertEqual(owners.merged_decisions("", "  \n\n"), [])


class EnsureSectionTests(unittest.TestCase):
    def test_insert_first(self) -> None:
        text = "---\njira: A-1\n---\n\n# Title\n\n## Notes\n\n- n\n"
        self.assertEqual(
            owners.ensure_section(text, "Decisions"),
            "---\njira: A-1\n---\n\n# Title\n\n## Decisions\n\n## Notes\n\n- n\n",
        )

    def test_idempotent_and_byte_identical(self) -> None:
        text = "---\njira: A-1\n---\n\n## Notes\n\n- n\n\n## Decisions\n- x\n"
        self.assertEqual(owners.ensure_section(text, "Decisions"), text)
        once = owners.ensure_section("## Notes\n", "Decisions")
        self.assertEqual(owners.ensure_section(once, "Decisions"), once)

    def test_frontmatter_only(self) -> None:
        got = owners.ensure_section("---\njira: A-1\n---\n", "Decisions")
        self.assertEqual(got, "---\njira: A-1\n---\n\n## Decisions\n")

    def test_notes_only(self) -> None:
        self.assertEqual(
            owners.ensure_section("## Notes\n", "Decisions"), "## Decisions\n\n## Notes\n"
        )

    def test_not_first(self) -> None:
        got = owners.ensure_section("## Notes\n\n- n\n", "Links", first=False)
        self.assertEqual(got, "## Notes\n\n- n\n\n## Links\n")


class SetSectionTests(unittest.TestCase):
    TEXT = "---\njira: A-1\n---\n\n## Decisions\n\n- old\n\n## Notes\n\n- n\n"

    def test_replace_only_that_body(self) -> None:
        got = owners.set_section_lines(self.TEXT, "Decisions", ["- a", "- b"])
        self.assertEqual(got, self.TEXT.replace("- old", "- a\n- b"))

    def test_same_lines_is_identity(self) -> None:
        self.assertEqual(owners.set_section_lines(self.TEXT, "Decisions", ["- old"]), self.TEXT)

    def test_empty_and_last_section(self) -> None:
        got = owners.set_section_lines(self.TEXT, "Decisions", [])
        self.assertEqual(got, "---\njira: A-1\n---\n\n## Decisions\n\n## Notes\n\n- n\n")
        last = owners.set_section_lines(self.TEXT, "Notes", ["- z"])
        self.assertEqual(last, self.TEXT.replace("- n", "- z"))

    def test_missing_section_is_created(self) -> None:
        got = owners.set_section_lines("## Notes\n", "Decisions", ["- a"])
        self.assertEqual(got, "## Decisions\n\n- a\n\n## Notes\n")


class ArchiveTests(unittest.TestCase):
    def test_rel(self) -> None:
        self.assertEqual(owners.ARCHIVE_DIR, "archive/decisions")
        self.assertEqual(owners.archive_rel("APP-1"), "archive/decisions/APP-1.md")

    def test_skeleton(self) -> None:
        self.assertEqual(
            owners.archive_append("", "caching", ["- a", "- b"]),
            "# Superseded decisions: caching\n\n## History\n\n- a\n- b\n",
        )

    def test_append_and_idempotent(self) -> None:
        once = owners.archive_append("", "t", ["- a"])
        twice = owners.archive_append(once, "t", ["- b", "- a", "- c"])
        self.assertEqual(twice, once + "- b\n- c\n")
        self.assertEqual(owners.archive_append(twice, "t", ["- b", "- c"]), twice)

    def test_history_not_last(self) -> None:
        text = "# T\n\n## History\n\n- a\n\n## Other\n\n- x\n"
        self.assertEqual(
            owners.archive_append(text, "t", ["- b"]),
            "# T\n\n## History\n\n- a\n- b\n\n## Other\n\n- x\n",
        )

    def test_superseded_line(self) -> None:
        self.assertEqual(
            owners.superseded_line("- x", "2026-01-02", "tasks/A-1.md", "user ab"),
            "- x · superseded 2026-01-02 · from tasks/A-1.md · user ab",
        )


class StubTests(unittest.TestCase):
    def test_stub(self) -> None:
        head = taskfile.merge("", "APP-1", {}, "2026-01-02").split("\n---\n")[0]
        self.assertEqual(
            taskfile.stub("APP-1", "2026-01-02"), head + "\n---\n\n## Decisions\n\n## Notes\n"
        )
        self.assertEqual(head, "---\njira: APP-1\ncreated: 2026-01-02\nupdated: 2026-01-02")

    def test_stub_url(self) -> None:
        got = taskfile.stub("APP-1", "2026-01-02", "https://x.example/browse/APP-1")
        self.assertIn("url: https://x.example/browse/APP-1\ncreated:", got)


class SeamTests(unittest.TestCase):
    def test_pack_sources_legacy(self) -> None:
        self.assertEqual(packs.pack_sources("ticket", "abc-1")["legacy"], ["decisions/ABC-1.md"])
        self.assertEqual(
            packs.pack_sources("repo", "app-2")["legacy"],
            ["decisions/app-2.md", "decisions/APP-2.md"],
        )

    def test_own_files_ignores_legacy(self) -> None:
        self.assertIn("tasks/ABC-1.md", packs.own_files("ticket", "ABC-1"))


if __name__ == "__main__":
    unittest.main()
