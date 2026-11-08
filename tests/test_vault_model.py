import json
import os
import unittest

from juno import config
from juno.adapters import vaultview
from juno.core import retire, vaultfmt
from tests.support import SandboxCase

RULE = '- 2026-01-01 · Keep it short · src: user abcd1234 · "keep it short please"'


class Frontmatter(unittest.TestCase):
    def test_scalars_lists_and_wikilinks(self) -> None:
        text = (
            '---\ntitle: "A: b"\nstatus: Done # note\nrepos: [a, b]\ntags:\n  - x\n  - "y z"\n'
            "supersedes: [[old-note]]\nnested:\n  k: v\n---\nbody\n"
        )
        fields, body = vaultfmt.parse_frontmatter(text)
        self.assertEqual(fields["title"], "A: b")
        self.assertEqual(fields["status"], "Done")
        self.assertEqual(fields["repos"], ["a", "b"])
        self.assertEqual(fields["tags"], ["x", "y z"])
        self.assertEqual(fields["supersedes"], ["[[old-note]]"])
        self.assertNotIn("nested", fields)
        self.assertEqual(body, "body\n")

    def test_unclosed_or_absent_frontmatter(self) -> None:
        self.assertEqual(vaultfmt.parse_frontmatter("---\ntitle: x\n"), ({}, "---\ntitle: x\n"))
        self.assertEqual(vaultfmt.parse_frontmatter("plain"), ({}, "plain"))

    def test_sections(self) -> None:
        text = "# T\n\n## Purpose\nwhy\n\n## Gotchas\n- a\n- b\n\n# Other\n"
        self.assertEqual(vaultfmt.section(text, "Gotchas"), "- a\n- b")
        self.assertEqual(vaultfmt.section(text, "Missing"), "")

    def test_decision_current_ignores_history(self) -> None:
        text = "---\ntopic: t\n---\n## Current\n- now\n\n## History\n- before\n"
        self.assertEqual(vaultfmt.decision_current(text), "- now")


class Kinds(unittest.TestCase):
    def test_kind_of(self) -> None:
        expected = {
            "core.md": "core",
            "inbox.md": "inbox",
            "threads.md": "threads",
            "pitfalls.md": "pitfalls",
            "rules/global.md": "rule",
            "decisions/x.md": "decision",
            "knowledge/x.md": "knowledge",
            "handoffs/x.md": "handoff",
            "archive/handoffs/2026-01.md": "archive",
            "_generated/learned.md": "generated",
            "self/values.md": "self",
            "mystery/x.md": "other",
        }
        for rel, kind in expected.items():
            self.assertEqual(vaultfmt.kind_of(rel), kind, rel)

    def test_rule_scope(self) -> None:
        self.assertEqual(vaultfmt.rule_scope("rules/global.md"), "global")
        self.assertEqual(vaultfmt.rule_scope("rules/repos/juno.md"), "repo:juno")
        self.assertEqual(vaultfmt.rule_scope("rules/topics/git.md"), "topic:git")
        self.assertEqual(vaultfmt.rule_scope("rules/ABC-1.md"), "ticket:ABC-1")
        self.assertIsNone(vaultfmt.rule_scope("rules/repos/a/b.md"))
        self.assertIsNone(vaultfmt.rule_scope("notes/x.md"))

    def test_hidden_reasons(self) -> None:
        self.assertEqual(vaultfmt.hidden_reason("notes/a.md", "x", None), None)
        self.assertEqual(vaultfmt.hidden_reason("notes/a.md", "x", {"notes/a.md"}), "retired")
        self.assertEqual(
            vaultfmt.hidden_reason("notes/a.md", "---\nstatus: archived\n---\n", None), "archived"
        )
        self.assertEqual(
            vaultfmt.hidden_reason("notes/a.md", "---\nvisibility: private\n---\n", None), "private"
        )
        self.assertEqual(vaultfmt.hidden_reason("archive/handoffs/a.md", "x", None), "archive")
        self.assertEqual(vaultfmt.hidden_reason("_generated/learned.md", "x", None), "generated")


class Lines(unittest.TestCase):
    def test_rule_lines_inject_rule_part_only(self) -> None:
        text = "# Rules\n" + RULE + "\n- 2026-01-02 · Migrated · src: unquoted\n- free text\n"
        self.assertEqual(vaultfmt.rule_texts(text), ["Keep it short", "Migrated"])
        entries = vaultfmt.rule_entries(text)
        self.assertEqual((entries[0].number, entries[0].pid), (2, "abcd1234"))
        self.assertIsNone(entries[2].text)
        self.assertEqual(vaultfmt.injected_rules(text), "- Keep it short\n- Migrated")

    def test_cards(self) -> None:
        text = (
            "# H\n\n## 2026-10-01 10:00 · ABC-1 · work · abcd1234\nRepos: a, b\nTickets: ABC-1\n"
            "x\nLearned: y\n\n## not a card\n"
        )
        cards = vaultfmt.parse_cards(text, "handoffs/a.md")
        self.assertEqual([c["valid"] for c in cards], [True, False])
        self.assertEqual(cards[0]["repos"], ["a", "b"])
        self.assertTrue(vaultfmt.card_mentions(cards[0], "ticket", "ABC-1"))
        self.assertTrue(vaultfmt.card_mentions(cards[0], "repo", "b"))
        self.assertFalse(vaultfmt.card_mentions(cards[0], "repo", "c"))

    def test_thread_lines_by_tag_and_budget(self) -> None:
        text = "- [alpha, abc-1] one · since 2026-01-01\n- [beta] two\n- [alpha] three"
        self.assertEqual(vaultfmt.thread_lines(text, "alpha", 500, 5).count("\n"), 1)
        self.assertEqual(vaultfmt.thread_lines(text, "alpha", 500, 1).count("\n"), 0)
        self.assertEqual(vaultfmt.thread_lines(text, "alpha", 10, 5), "")
        self.assertEqual(vaultfmt.thread_lines(text, "ABC-1", 500, 5).count("one"), 1)


def metas(**files: str) -> dict[str, retire.FileMeta]:
    return {rel.replace("__", "/") + ".md": retire.meta_of(text) for rel, text in files.items()}


class Retirement(unittest.TestCase):
    def test_supersedes_retires_target(self) -> None:
        res = retire.resolve(
            metas(notes__new="---\nsupersedes: notes/old\n---\n", notes__old="old")
        )
        self.assertEqual(res.retired, {"notes/old.md": "superseded by notes/new.md"})
        self.assertEqual(
            res.entries(), [{"path": "notes/old.md", "by": "notes/new.md", "reason": "supersedes"}]
        )

    def test_wikilink_stem_resolution_and_ambiguity(self) -> None:
        res = retire.resolve(
            metas(notes__new="---\nsupersedes: [[old]]\n---\n", notes__old="x", knowledge__old="y")
        )
        self.assertEqual(res.retired, {})
        self.assertEqual(res.dead[0]["reason"], "ambiguous")
        res = retire.resolve(metas(notes__new="---\nsupersedes: [[old]]\n---\n", notes__old="x"))
        self.assertIn("notes/old.md", res.retired)

    def test_protected_folders_are_never_retired(self) -> None:
        for top in ("rules", "tasks", "handoffs", "projects", "decisions", "self"):
            res = retire.resolve(
                metas(notes__new=f"---\nsupersedes: {top}/x\n---\n", **{f"{top}__x": "x"})
            )
            self.assertEqual(res.retired, {}, top)
            self.assertEqual(res.dead[0]["reason"], "protected", top)

    def test_cycles_and_unresolved(self) -> None:
        res = retire.resolve(
            metas(
                notes__a="---\nsupersedes: notes/b\n---\n",
                notes__b="---\nsupersedes: notes/a\n---\n",
                notes__c="---\nsupersedes: notes/ghost\n---\n",
                notes__d="---\nsupersedes: ../x\n---\n",
            )
        )
        self.assertEqual(res.retired, {})
        self.assertEqual(
            sorted(str(d["reason"]) for d in res.dead),
            ["cycle", "cycle", "unresolved", "unresolved"],
        )

    def test_archived_source_does_not_retire_and_chains_propagate(self) -> None:
        res = retire.resolve(
            metas(
                notes__a="---\nsupersedes: notes/b\n---\n",
                notes__b="---\nsupersedes: notes/c\n---\n",
                notes__c="c",
            )
        )
        self.assertEqual(set(res.retired), {"notes/b.md"})
        self.assertEqual(res.dead[0]["reason"], "inactive-source")
        res = retire.resolve(
            metas(notes__a="---\nstatus: archived\nsupersedes: notes/b\n---\n", notes__b="b")
        )
        self.assertEqual(set(res.retired), {"notes/a.md"})

    def test_archived_status_is_retired(self) -> None:
        res = retire.resolve(metas(notes__a="---\nstatus: archived\n---\n"))
        self.assertEqual(res.entries(), [{"path": "notes/a.md", "by": None, "reason": "archived"}])


class RetiredFile(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.sb.cli("init", *self.sb.install_args())
        self.cfg = config.load(dict(self.sb.env))

    def write(self, rel: str, text: str) -> None:
        path = os.path.join(self.sb.vault, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_sync_writes_and_view_reads_it(self) -> None:
        self.write("knowledge/new.md", "---\nsupersedes: knowledge/old\n---\nnew\n")
        self.write("knowledge/old.md", "old\n")
        vaultview.sync_retired(self.cfg)
        with open(os.path.join(self.sb.state, "retired.json"), encoding="utf-8") as handle:
            doc = json.load(handle)
        self.assertEqual(doc["v"], 1)
        self.assertEqual(doc["retired"][0]["path"], "knowledge/old.md")
        view = vaultview.DiskVault(self.cfg)
        self.assertTrue(view.hidden("knowledge/old.md"))
        self.assertFalse(view.hidden("knowledge/new.md"))

    def test_unchanged_sync_keeps_the_file(self) -> None:
        vaultview.sync_retired(self.cfg)
        path = os.path.join(self.sb.state, "retired.json")
        before = os.stat(path).st_mtime_ns
        vaultview.sync_retired(self.cfg)
        self.assertEqual(os.stat(path).st_mtime_ns, before)

    def test_corrupt_or_unknown_version_retires_nothing(self) -> None:
        os.makedirs(self.sb.state, exist_ok=True)
        path = os.path.join(self.sb.state, "retired.json")
        for raw in ("{not json", '{"v": 2, "retired": []}'):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(raw)
            self.assertEqual(vaultview.read_retired(self.sb.state), set())
        os.unlink(path)
        self.assertIsNone(vaultview.read_retired(self.sb.state))

    def test_private_and_archive_files_are_hidden_without_a_retired_file(self) -> None:
        self.write("notes/p.md", "---\nvisibility: private\n---\nsecret\n")
        view = vaultview.DiskVault(self.cfg)
        self.assertTrue(view.hidden("notes/p.md"))
        self.assertTrue(view.hidden("archive/handoffs/2026-01.md", "x"))

    def test_stale_mark_needs_a_matching_signature(self) -> None:
        self.write("knowledge/k.md", "k\n")
        sig = vaultview.store.file_sig(os.path.join(self.sb.vault, "knowledge", "k.md"))
        doc = {
            "v": 1,
            "files": {"knowledge/k.md": {"stale": True, "since": "2026-01-01", "sig": sig}},
        }
        os.makedirs(self.sb.state, exist_ok=True)
        with open(os.path.join(self.sb.state, "stale.json"), "w", encoding="utf-8") as handle:
            json.dump(doc, handle)
        self.assertEqual(
            vaultview.DiskVault(self.cfg).stale_mark("knowledge/k.md"),
            " (may be stale since 2026-01-01)",
        )
        self.write("knowledge/k.md", "k changed\n")
        self.assertEqual(vaultview.DiskVault(self.cfg).stale_mark("knowledge/k.md"), "")


if __name__ == "__main__":
    unittest.main()
