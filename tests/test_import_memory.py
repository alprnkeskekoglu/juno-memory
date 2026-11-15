import os
import re
import unittest
from typing import ClassVar
from unittest import mock

from juno.adapters import queues
from juno.cmd import init
from juno.core import candidates, nativememory, vaultfmt
from tests.support.write import WriteCase

NOTE = "---\nname: {name}\ndescription: {desc}\ntype: {type}\n---\n{body}\n"


def note(name: str, kind: str, desc: str, body: str = "**Why:** because.") -> str:
    return NOTE.format(name=name, type=kind, desc=desc, body=body)


class Parse(unittest.TestCase):
    def test_parses_frontmatter_and_body(self) -> None:
        text = note("tabs", "feedback", "'Prefer tabs over spaces'", "**Why:** style")
        found = nativememory.parse("tabs.md", text)
        assert found is not None
        self.assertEqual((found.name, found.type), ("tabs", "feedback"))
        self.assertEqual(found.description, "Prefer tabs over spaces")
        self.assertIn("Why", found.body)

    def test_skips_index_odd_and_untyped_files(self) -> None:
        self.assertIsNone(nativememory.parse("MEMORY.md", "- [a](a.md) - x\n"))
        self.assertIsNone(nativememory.parse("a.md", "no frontmatter at all"))
        self.assertIsNone(nativememory.parse("a.md", "---\nname: a\n"))
        self.assertIsNone(nativememory.parse("a.md", note("a", "weird", "something here")))
        self.assertIsNone(nativememory.parse("a.txt", note("a", "user", "something here")))

    def test_fact_falls_back_to_body_and_clips(self) -> None:
        found = nativememory.parse(
            "a.md", note("a", "project", "", "**Freeze** merges on Thursday")
        )
        assert found is not None
        self.assertEqual(nativememory.fact(found), "Freeze merges on Thursday")
        long = nativememory.parse("b.md", note("b", "project", "word " * 200))
        assert long is not None
        self.assertLessEqual(len(nativememory.fact(long)), nativememory.TEXT_MAX)

    def test_mapping(self) -> None:
        def kind(t: str, repo: str | None) -> str:
            found = nativememory.parse("a.md", note("a", t, "some description"))
            assert found is not None
            return nativememory.target(found, repo)

        self.assertEqual(kind("project", "app"), "profile")
        self.assertEqual(kind("reference", None), "knowledge")
        self.assertEqual(kind("feedback", "app"), "rule")
        self.assertEqual(kind("user", None), "user")

    def test_slug_and_repo_match(self) -> None:
        slug = nativememory.project_slug("/Users/me/code/app.v2")
        self.assertEqual(slug, "-Users-me-code-app-v2")
        repos = {"-Users-me-code-app": "app", "-Users-me-code-app-v2": "app-v2"}
        self.assertEqual(nativememory.match_repo(slug, repos), "app-v2")
        self.assertEqual(
            nativememory.match_repo("-Users-me-code-app--claude-worktrees-x", repos), "app"
        )
        self.assertIsNone(nativememory.match_repo("-Users-me-other", repos))


class Base(WriteCase):
    def setUp(self) -> None:
        super().setUp()
        self.projects = os.path.join(self.sb.root, "claude-projects")
        self.repos = os.path.join(self.sb.root, "code")
        os.makedirs(os.path.join(self.repos, "app", ".git"))
        for env in (self.sb.env, self.env):
            env["JUNO_CLAUDE_PROJECTS"] = self.projects
            env["JUNO_REPO_ROOTS"] = self.repos

    def memory(self, slug: str, name: str, text: str) -> str:
        directory = os.path.join(self.projects, slug, "memory")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def seed(self) -> str:
        slug = nativememory.project_slug(os.path.realpath(os.path.join(self.repos, "app")))
        self.memory(slug, "MEMORY.md", "- [freeze](freeze.md) - freeze\n")
        self.memory(slug, "freeze.md", note("freeze", "project", "Merge freeze starts on Thursday"))
        self.memory(slug, "style.md", note("style", "feedback", "Prefer small commits always"))
        self.memory(slug, "notes.txt", "not markdown")
        self.memory(slug, "junk.md", "no frontmatter")
        other = "-tmp-elsewhere"
        self.memory(
            other, "board.md", note("board", "reference", "Bugs are tracked in the INGEST board")
        )
        self.memory(other, "me.md", note("me", "user", "Works as a data engineer on pipelines"))
        os.makedirs(os.path.join(self.projects, "-empty", "memory"))
        return slug

    def rules(self) -> list[dict[str, object]]:
        return queues.read(self.cfg, queues.RULES)

    def mine(self) -> list[dict[str, object]]:
        return queues.read(self.cfg, queues.SELF)

    def tree(self) -> dict[str, str]:
        found = {}
        for base, dirs, files in os.walk(self.sb.vault):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in files:
                path = os.path.join(base, name)
                with open(path, encoding="utf-8") as handle:
                    found[os.path.relpath(path, self.sb.vault)] = handle.read()
        return found


class Import(Base):
    def test_nothing_to_import(self) -> None:
        code, out = self.sb.cli("import-memory")
        self.assertEqual(code, 0)
        self.assertIn("no importable", out)

    def test_writes_vault_and_queues_candidates(self) -> None:
        slug = self.seed()
        code, out = self.sb.cli("import-memory")
        self.assertEqual(code, 0, out)
        self.assertIn("Claude's own memory has 4 notes in 2 projects", out)
        profile = self.get("projects/app.md")
        self.assertIn("Merge freeze starts on Thursday", profile)
        self.assertRegex(profile, r"## Gotchas\n\n- Merge freeze")
        know = self.get("knowledge/board.md")
        self.assertIn("src: import:memory/-tmp-elsewhere/board.md", know)
        self.assertEqual(self.get("rules/global.md"), "")
        self.assertEqual(self.get("rules/repos/app.md"), "")
        self.assertEqual(self.get("self/values.md").count("data engineer"), 0)
        items = {i["rule"]: i for i in self.rules()}
        self.assertEqual(items["Prefer small commits always"]["scope"], "repo:app")
        self.assertNotIn("Works as a data engineer on pipelines", items)
        mine = queues.read(self.cfg, queues.SELF)
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["text"], "Works as a data engineer on pipelines")
        self.assertEqual(mine[0]["file"], "communication")
        self.assertEqual(mine[0]["batch"], "memory-import")
        self.assertEqual(mine[0]["source"], "import:memory/-tmp-elsewhere/me.md")
        self.assertRegex(str(mine[0]["id"]), r"^s[0-9a-f]{6}$")
        self.assertEqual(self.get("self/communication.md"), "")
        for item in items.values():
            self.assertEqual(item["status"], "pending")
            self.assertEqual(item["batch"], "memory-import")
        self.assertEqual(
            items["Prefer small commits always"]["source"], f"import:memory/{slug}/style.md"
        )
        self.assertRegex(out, r"juno undo --run imp-[0-9a-f]{8}")

    def test_originals_are_untouched(self) -> None:
        self.seed()
        before = {}
        for base, _, files in os.walk(self.projects):
            for name in files:
                with open(os.path.join(base, name), "rb") as handle:
                    before[os.path.join(base, name)] = handle.read()
        self.sb.cli("import-memory")
        after = {}
        for base, _, files in os.walk(self.projects):
            for name in files:
                with open(os.path.join(base, name), "rb") as handle:
                    after[os.path.join(base, name)] = handle.read()
        self.assertEqual(before, after)

    def test_dry_run_writes_nothing(self) -> None:
        self.seed()
        before = self.tree()
        code, out = self.sb.cli("import-memory", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("would be", out)
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.rules(), [])

    def test_rerun_is_idempotent(self) -> None:
        self.seed()
        self.sb.cli("import-memory")
        before, queue = self.tree(), self.rules()
        code, out = self.sb.cli("import-memory")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.rules(), queue)
        self.assertIn("already queued", out)

    def test_privacy_skip_lists_path_and_reason_not_content(self) -> None:
        slug = self.seed()
        secret = "reach me at someone@example.com about the freeze"
        self.memory(slug, "leak.md", note("leak", "project", secret))
        self.memory(slug, "leak2.md", note("leak2", "feedback", secret))
        code, out = self.sb.cli("import-memory")
        self.assertEqual(code, 0, out)
        self.assertIn(f"skipped {slug}/leak.md", out)
        self.assertIn(f"skipped {slug}/leak2.md", out)
        self.assertNotIn("someone@example.com", out)
        self.assertNotIn("example.com", "".join(self.tree().values()))
        self.assertNotIn("example.com", repr(self.rules()))

    def test_sensitive_user_note_is_not_queued(self) -> None:
        self.memory("-x", "me.md", note("me", "user", "Is dealing with depression this year"))
        code, out = self.sb.cli("import-memory")
        self.assertEqual(code, 0, out)
        self.assertIn("skipped -x/me.md", out)
        self.assertEqual(self.rules(), [])
        self.assertEqual(queues.read(self.cfg, queues.SELF), [])

    def test_over_the_hard_limit_is_reported_and_rerun_continues(self) -> None:
        for n in range(5):
            self.memory("-x", f"f{n}.md", note(f"f{n}", "feedback", f"Prefer approach number {n}"))
        with mock.patch.object(nativememory, "BATCH_MAX", 3):
            code, out = self.sb.cli("import-memory")
            self.assertEqual(code, 0, out)
            self.assertIn("2 over the limit of 3", out)
            self.assertEqual(len(self.rules()), 3)
            self.assertEqual(self.sb.cli("import-memory")[0], 0)
            self.assertEqual(len(self.rules()), 3)
            queues.update(self.cfg, queues.RULES, lambda q: queues.mark(q, "rejected"))
            self.assertEqual(self.sb.cli("import-memory")[0], 0)
        self.assertEqual(len(self.rules()), 5)

    def test_no_write_cap_truncation(self) -> None:
        self.set_config("[caps]\nrun_max = 1\n")
        for n in range(6):
            self.memory(
                "-big", f"n{n}.md", note(f"fact-{n}", "reference", f"Distinct fact number {n} here")
            )
        code, out = self.sb.cli("import-memory")
        self.assertEqual(code, 0, out)
        self.assertEqual(len([f for f in self.tree() if f.startswith("knowledge/fact-")]), 6)

    def test_undo_run_reverts_vault_writes(self) -> None:
        self.seed()
        before = self.tree()
        out = self.sb.cli("import-memory")[1]
        run = re.search(r"undo --run (imp-[0-9a-f]{8})", out)
        assert run
        code, text = self.juno("undo", "--run", run[1])
        self.assertEqual(code, 0, text)
        self.assertEqual(self.get("knowledge/board.md"), "")
        self.assertNotIn("Merge freeze", self.get("projects/app.md"))
        self.assertEqual(len(self.tree()), len(before))

    def test_refused_inside_a_claude_session(self) -> None:
        self.seed()
        code, out = self.juno("import-memory")
        self.assertEqual(code, 2)
        self.assertIn("outside Claude Code", out)

    def test_import_evidence_cannot_be_typed_into_verbs(self) -> None:
        code, _out = self.juno(
            "knowledge",
            "add",
            "--topic",
            "sneaky",
            "--evidence",
            "import:memory/-x/a.md",
            "A fact that stays true for a month",
        )
        self.assertEqual(code, 2)
        self.assertEqual(self.get("knowledge/sneaky.md"), "")


class Approval(Base):
    def setUp(self) -> None:
        super().setUp()
        self.seed()
        self.sb.cli("import-memory")

    def test_not_rules_until_chosen(self) -> None:
        code, out = self.juno("rule", "accept", "--batch", "memory-import")
        self.assertEqual(code, 2)
        self.assertIn("did not choose", out)
        self.assertEqual(self.get("rules/repos/app.md"), "")

    def test_batch_accept_by_option_label(self) -> None:
        self.answer("Accept all memory-import (Recommended)")
        code, out = self.juno("rule", "accept", "--batch", "memory-import")
        self.assertEqual(code, 0, out)
        text = self.get("rules/repos/app.md")
        self.assertRegex(
            text, r"- \d{4}-\d\d-\d\d · Prefer small commits always · src: import:memory/"
        )
        entries = vaultfmt.rule_entries(text)
        self.assertEqual(entries[0].text, "Prefer small commits always")
        self.assertEqual(self.get("rules/global.md"), "")
        self.assertTrue(all(i["status"] == "accepted" for i in self.rules()))

    def test_review_choice_does_not_accept_the_whole_batch(self) -> None:
        self.answer("Review memory-import")
        code, out = self.juno("import-memory", "accept")
        self.assertEqual(code, 2)
        self.assertIn("did not choose", out)
        self.assertTrue(all(i["status"] == "pending" for i in self.rules()))

    def test_batch_reject_and_pending_listing(self) -> None:
        self.assertIn("native memory", self.juno("rule", "pending")[1])
        code, out = self.juno("rule", "reject", "--batch", "memory-import")
        self.assertEqual(code, 0, out)
        self.assertTrue(all(i["status"] == "rejected" for i in self.rules()))

    def test_review_path_accepts_single_ids(self) -> None:
        pick = next(i for i in self.rules() if i["kind"] == "feedback")
        self.answer(f"{pick['id']} Prefer small commits")
        code, out = self.juno("rule", "accept", str(pick["id"]))
        self.assertEqual(code, 0, out)
        self.assertIn("Prefer small commits always", self.get("rules/repos/app.md"))
        self.assertEqual(self.get("rules/global.md"), "")

    def test_rule_batch_command_settles_both_kinds(self) -> None:
        self.answer("Accept all memory-import (Recommended)")
        self.assertEqual(self.juno("rule", "accept", "--batch", "memory-import")[0], 0)
        self.assertIn("data engineer", self.get("self/communication.md"))
        self.assertTrue(all(i["status"] == "accepted" for i in self.mine()))

    def test_ids_and_batch_together_refused(self) -> None:
        self.answer("Accept all memory-import")
        code, _ = self.juno("rule", "accept", "--batch", "memory-import", "r000000")
        self.assertEqual(code, 2)


class Settle(Base):
    """One command per outcome settles the rule and the self candidates of the batch together."""

    def setUp(self) -> None:
        super().setUp()
        self.seed()
        self.sb.cli("import-memory")

    def test_nothing_in_self_until_chosen(self) -> None:
        code, out = self.juno("import-memory", "accept")
        self.assertEqual(code, 2)
        self.assertIn("did not choose", out)
        self.assertEqual(self.get("self/communication.md"), "")
        self.assertEqual(self.get("rules/repos/app.md"), "")
        self.assertTrue(all(i["status"] == "pending" for i in self.mine()))

    def test_accept_writes_self_line_and_rule(self) -> None:
        self.answer("Accept all memory-import (Recommended)")
        code, out = self.juno("import-memory", "accept")
        self.assertEqual(code, 0, out)
        text = self.get("self/communication.md")
        self.assertRegex(
            text,
            r"- Works as a data engineer on pipelines · \d{4}-\d\d-\d\d · "
            r"src: import:memory/-tmp-elsewhere/me\.md\n",
        )
        self.assertIn("Prefer small commits always", self.get("rules/repos/app.md"))
        self.assertEqual(self.get("rules/global.md"), "")
        self.assertTrue(all(i["status"] == "accepted" for i in self.mine() + self.rules()))
        self.assertIn("self/communication.md", out)
        self.assertEqual(self.juno("import-memory", "accept")[0], 2)

    def test_reject_settles_both_and_writes_nothing(self) -> None:
        code, out = self.juno("import-memory", "reject")
        self.assertEqual(code, 0, out)
        self.assertTrue(all(i["status"] == "rejected" for i in self.mine() + self.rules()))
        self.assertEqual(self.get("self/communication.md"), "")

    def test_review_path_takes_self_ids(self) -> None:
        sid = str(self.mine()[0]["id"])
        rid = str(next(i for i in self.rules() if i["kind"] == "feedback")["id"])
        self.assertEqual(self.juno("import-memory", "accept", sid)[0], 2)
        self.answer(f"{sid} Works as a data engineer")
        code, out = self.juno("import-memory", "accept", sid)
        self.assertEqual(code, 0, out)
        self.assertIn("data engineer", self.get("self/communication.md"))
        self.assertEqual(self.get("rules/repos/app.md"), "")
        code, out = self.juno("import-memory", "reject", rid)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.rules()[0]["status"], "rejected")

    def test_stance_is_refused_at_accept_and_nothing_is_written(self) -> None:
        self.mine()  # queue exists
        queues.update(
            self.cfg,
            queues.SELF,
            lambda q: q[0].update(text="Believes that tabs are the best way to indent"),
        )
        self.answer("Accept all memory-import")
        code, out = self.juno("import-memory", "accept")
        self.assertEqual(code, 2)
        self.assertIn("stance", out)
        self.assertEqual(self.get("self/communication.md"), "")

    def test_session_start_asks_one_question_for_both_kinds(self) -> None:
        from juno.handlers import session_start

        blocks = [
            b for b in session_start.extra_blocks(self.cfg, "startup") if "memory-import" in b
        ]
        self.assertEqual(len(blocks), 1)
        self.assertIn(f"{self.mine()[0]['id']} · self · communication", blocks[0])
        self.assertIn("· rule · repo:app · Prefer small commits always", blocks[0])
        self.assertIn("juno import-memory accept <ids>", blocks[0])

    def test_pending_lists_both_kinds(self) -> None:
        out = self.juno("rule", "pending")[1]
        self.assertIn("self/communication", out)
        self.assertIn("Works as a data engineer", out)


class Budget(unittest.TestCase):
    NOW = 1_000_000_000.0

    def imports(self, count: int) -> list[dict[str, object]]:
        return [
            {
                "id": f"r{n:06x}",
                "rule": f"imported rule {n}",
                "scope": "global",
                "batch": "memory-import",
                "t": self.NOW,
                "status": "pending",
            }
            for n in range(count)
        ]

    def test_import_candidates_do_not_fill_the_ordinary_cap(self) -> None:
        queue = self.imports(candidates.PENDING_CAP + 5)
        fresh = [{"scope": "global", "rule": "Always run the tests", "quote": "run the tests"}]
        queue, counts = candidates.add_rules(
            queue, fresh, "sid-1", self.NOW, set(), set(), "none", lambda q, p: q
        )
        self.assertEqual(counts, {"queued": 1, "duplicate": 0, "capped": 0})
        self.assertEqual(len(candidates.pending(queue)), candidates.PENDING_CAP + 6)

    def test_ordinary_cap_is_unchanged(self) -> None:
        queue: list[dict[str, object]] = [
            {"id": f"r{n:06x}", "rule": f"x{n}", "t": self.NOW, "status": "pending"}
            for n in range(candidates.PENDING_CAP)
        ]
        fresh = [{"scope": "global", "rule": "One more rule", "quote": "one more"}]
        _, counts = candidates.add_rules(
            queue, fresh, "sid-1", self.NOW, set(), set(), "none", lambda q, p: q
        )
        self.assertEqual(counts["capped"], 1)

    def test_nudges_do_not_share_the_shown_limit(self) -> None:
        items = [
            *self.imports(candidates.NUDGE_SHOWN + 3),
            {"id": "rffffff", "rule": "Ordinary one", "scope": "global", "quote": "q"},
        ]
        self.assertIn("rffffff", candidates.rules_nudge(items))
        self.assertIn("(1 pending", candidates.rules_nudge(items))
        self.assertNotIn("rffffff", candidates.import_nudge(items))

    def test_pending_import_candidates_do_not_expire_ordinary_ones_do(self) -> None:
        late = self.NOW + 400 * candidates.DAY
        ordinary = {"id": "raaaaaa", "rule": "x", "t": self.NOW, "status": "pending"}
        kept = candidates.prune([*self.imports(2), ordinary], late)
        self.assertEqual([i["status"] for i in kept[:2]], ["pending", "pending"])
        self.assertEqual(kept[2]["status"], "expired")

    def test_decided_import_candidates_follow_the_normal_retention(self) -> None:
        item = dict(self.imports(1)[0], status="rejected", decided_t=self.NOW)
        gone = candidates.prune([item], self.NOW + (candidates.DECIDED_DAYS + 1) * candidates.DAY)
        self.assertEqual(gone, [])


class Nudge(unittest.TestCase):
    ITEMS: ClassVar[list[dict[str, str]]] = [
        {"id": "raaaaaa", "rule": "x", "scope": "global", "batch": "memory-import"},
        {
            "id": "sabcdef",
            "text": "y",
            "file": "communication",
            "batch": "memory-import",
            "target": "self",
        },
    ]

    def test_one_question_for_the_batch(self) -> None:
        text = candidates.import_nudge(self.ITEMS)
        self.assertIn("ask the user ONCE", text)
        self.assertIn("Accept all memory-import (Recommended)", text)
        self.assertIn("juno import-memory accept", text)
        self.assertIn("juno import-memory reject", text)
        self.assertIn("juno import-memory accept <ids>", text)
        self.assertIn("Review memory-import", text)
        self.assertIn("raaaaaa · rule · global · x", text)
        self.assertIn("sabcdef · self · communication · y", text)
        self.assertIn("(1 rule, 1 self; 2 pending", text)
        self.assertEqual(candidates.rules_nudge(self.ITEMS), "")

    def test_plain_candidates_keep_their_own_question(self) -> None:
        plain = [{"id": "rcccccc", "rule": "z", "scope": "global", "quote": "q"}]
        self.assertIn("one option per candidate", candidates.rules_nudge(plain))
        self.assertEqual(candidates.import_nudge(plain), "")
        mixed = candidates.rules_nudge([*plain, *self.ITEMS])
        self.assertNotIn("raaaaaa", mixed)


class InitQuestion(Base):
    def run_init(self, answer: str | None, tty: bool = True) -> tuple[str, list[str]]:
        asked: list[str] = []

        def fake_input(question: str) -> str:
            asked.append(question)
            if "Import them" in question:
                if answer is None:
                    raise EOFError
                return answer
            return "n"

        with (
            mock.patch.object(init, "interactive", return_value=tty),
            mock.patch("builtins.input", fake_input),
        ):
            code, out = self.sb.cli("init", *self.sb.install_args())
        self.assertEqual(code, 0, out)
        return out, [q for q in asked if "Import them" in q]

    def test_nothing_to_import_asks_nothing(self) -> None:
        out, asked = self.run_init("y")
        self.assertEqual(asked, [])
        self.assertNotIn("memory import", out)
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "memory-import-asked")))

    def test_default_is_yes(self) -> None:
        self.seed()
        _out, asked = self.run_init("")
        self.assertEqual(
            asked, ["Claude's own memory has 4 notes in 2 projects. Import them into Juno? [Y/n] "]
        )
        self.assertIn("Merge freeze", self.get("projects/app.md"))
        self.assertEqual((len(self.rules()), len(self.mine())), (1, 1))
        self.assertTrue(os.path.exists(os.path.join(self.sb.state, "memory-import-asked")))

    def test_no_prints_the_later_command(self) -> None:
        self.seed()
        out, asked = self.run_init("n")
        self.assertEqual(len(asked), 1)
        self.assertIn("import later with: juno import-memory", out)
        self.assertEqual(self.rules(), [])
        self.assertEqual(self.get("projects/app.md"), "")

    def test_eof_counts_as_no(self) -> None:
        self.seed()
        _out, asked = self.run_init(None)
        self.assertEqual(len(asked), 1)
        self.assertEqual(self.rules(), [])

    def test_asked_once(self) -> None:
        self.seed()
        self.run_init("n")
        out, asked = self.run_init("y")
        self.assertEqual(asked, [])
        self.assertIn("juno import-memory", out)
        self.assertEqual(self.rules(), [])

    def test_non_interactive_prints_the_command_only(self) -> None:
        self.seed()
        out, asked = self.run_init("y", tty=False)
        self.assertEqual(asked, [])
        self.assertIn("import them later with: juno import-memory", out)
        self.assertEqual(self.rules(), [])
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "memory-import-asked")))

    def test_dry_run_reports_and_writes_nothing(self) -> None:
        self.seed()
        before = self.tree()
        with mock.patch("builtins.input", side_effect=AssertionError("asked")):
            code, out = self.sb.cli("init", "--dry-run", *self.sb.install_args())
        self.assertEqual(code, 0, out)
        self.assertIn("would ask to import them", out)
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.rules(), [])


if __name__ == "__main__":
    unittest.main()
