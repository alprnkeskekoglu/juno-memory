import json
import os
import shutil
import time
import unittest

from juno.adapters import ledgerio, writelog
from juno.core import topics, writegate
from tests.support.write import WriteCase

QUOTE = "this is the first typed prompt"
NOTE = "The export job times out above ten thousand rows"
SECRET = "ghp_" + "a1B2c3D4e5F6" * 3


class Base(WriteCase):
    def ok(self, *argv: str) -> str:
        code, out = self.juno(*argv)
        self.assertEqual(code, 0, out)
        return out

    def refused(self, *argv: str, env: dict[str, str] | None = None) -> str:
        code, out = self.juno(*argv, env=env)
        self.assertEqual(code, 2, out)
        self.assertTrue(out.startswith("Juno: refused:"), out)
        return out

    def notes(self) -> list[str]:
        directory = self.vp("notes")
        return sorted(n for n in os.listdir(directory) if n.endswith(".md"))

    def wid(self, out: str) -> str:
        return out.split("(write ")[1].split(";")[0]

    def touch_repo(self, repo: str) -> None:
        ledgerio.append(self.cfg, self.sid, {"ev": "tool", "name": "Bash", "repos": [[repo, None]]})


class Note(Base):
    def test_writes_once_with_frontmatter(self) -> None:
        out = self.ok("note", "--slug", "export-timeout", "--repos", "app", NOTE)
        path = f"notes/{time.strftime('%Y-%m-%d')}-export-timeout.md"
        text = self.get(path)
        self.assertIn("repos: [app]", text)
        self.assertIn("source: " + self.sid[:8], text)
        self.assertTrue(text.rstrip().endswith(NOTE))
        self.assertRegex(out, r"write w-[0-9a-f]{8}")
        self.refused("note", "--slug", "export-timeout", NOTE + " again, rewritten")

    def test_near_duplicate_is_not_added(self) -> None:
        self.ok("note", "--slug", "one-note", NOTE)
        out = self.ok("note", "--slug", "other-note", NOTE.lower())
        self.assertIn("already recorded", out)
        self.assertEqual(len(self.notes()), 1)

    def test_scan_refuses_secrets_and_customer_data(self) -> None:
        for text in (
            f"The token is {SECRET} for the job",
            "Ask jo@example.com about the export job timing",
            "Partner 1234567 has an export timeout every night",
        ):
            self.refused("note", text)
        self.assertEqual(self.notes(), [])

    def test_short_and_bad_slug(self) -> None:
        self.refused("note", "too short")
        self.refused("note", "--slug", "Bad Slug", NOTE)

    def test_refusals_from_the_cheap_checks(self) -> None:
        self.refused("note", NOTE, env={"CLAUDE_CODE_AGENT_ID": "a1"})
        self.refused("note", NOTE, env={"CLAUDE_CODE_SESSION_ID": "no-such-ledger"})
        self.prompt("keep this one out [nosave]", "pid00002", nosave=True)
        self.refused("note", NOTE)
        self.assertEqual(self.notes(), [])

    def test_headless_is_refused(self) -> None:
        env = {"JUNO_TEST_INTERACTIVE": ""}
        self.assertEqual(self.juno("note", NOTE, env=env)[0], 2)


class Capture(Base):
    def test_capture_dedupes_and_updates_near_duplicates(self) -> None:
        self.ok("capture", "Look into the flaky invoice export test")
        out = self.ok("capture", "look into the flaky invoice export test")
        self.assertIn("already recorded", out)
        out = self.ok("capture", "Look into the flaky invoice export tests")
        self.assertIn("write w-", out)
        lines = [x for x in self.get("inbox.md").splitlines() if x.startswith("- ")]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("- Look into the flaky invoice export tests · "))

    def test_cap(self) -> None:
        cap = writegate.FILE_LINES["inbox.md"]
        self.put(
            "inbox.md",
            "# Inbox\n\n" + "".join(f"- Loose thought {n:03d} here\n" for n in range(cap)),
        )
        self.assertIn("cap", self.refused("capture", "One thought too many for the inbox"))


class Pitfall(Base):
    def test_line_format_is_the_injectable_one(self) -> None:
        self.ok(
            "pitfall",
            "--triggers",
            "terraform,plan",
            "--evidence",
            QUOTE,
            "Run terraform plan before apply on shared state",
        )
        line = [x for x in self.get("pitfalls.md").splitlines() if x.startswith("- ")][0]
        found = topics.pitfall_line(line)
        assert found is not None
        self.assertEqual(found["triggers"], ["terraform", "plan"])
        self.assertIn(f"segment {self.sid[:8]}", line)

    def test_evidence_must_be_anchored(self) -> None:
        text = "Run terraform plan before apply on shared state"
        out = self.refused("pitfall", "--evidence", "something nobody said in the session", text)
        self.assertIn("not anchored", out)
        self.refused("pitfall", "--evidence", "repo:app", text)
        self.touch_repo("app")
        self.ok("pitfall", "--evidence", "repo:app", text)

    def test_secret_in_evidence_refused(self) -> None:
        self.refused("pitfall", "--evidence", SECRET, "Run terraform plan before apply")


class Knowledge(Base):
    def test_session_fact_and_dedupe(self) -> None:
        self.ok(
            "knowledge", "add", "--topic", "queues", "--evidence", QUOTE, "Jobs retry three times"
        )
        text = self.get("knowledge/queues.md")
        self.assertIn("topic: queues", text)
        self.assertIn("- Jobs retry three times · src: user pid00001 · ", text)
        out = self.ok(
            "knowledge", "add", "--topic", "queues", "--evidence", QUOTE, "jobs retry three times"
        )
        self.assertIn("already recorded", out)
        self.ok(
            "knowledge",
            "add",
            "--topic",
            "queues",
            "--evidence",
            QUOTE,
            "Failed jobs go to a dead queue",
        )
        self.assertEqual(self.get("knowledge/queues.md").count("\n- "), 2)

    def test_web_source_needs_a_url_and_no_ledger_anchor(self) -> None:
        self.refused(
            "knowledge", "add", "--topic", "queues", "--source", "web", "Jobs retry thrice"
        )
        self.ok(
            "knowledge", "add", "--topic", "queues", "--source", "web", "--url",
            "https://example.com/docs/queues", "Jobs retry three times",
        )  # fmt: skip
        self.assertIn("src: web https://example.com/docs/queues", self.get("knowledge/queues.md"))

    def test_two_urls_on_different_domains_are_recorded(self) -> None:
        self.ok(
            "knowledge", "add", "--topic", "queues", "--source", "web",
            "--url", "https://docs.example.com/queues", "--url", "https://blog.example.org/q",
            "Jobs retry three times",
        )  # fmt: skip
        self.assertIn(
            "src: web https://docs.example.com/queues, https://blog.example.org/q",
            self.get("knowledge/queues.md"),
        )

    def test_same_registrable_domain_and_a_third_url_are_refused(self) -> None:
        same = ("--url", "https://docs.example.com/a", "--url", "https://www.example.com/b")
        self.refused("knowledge", "add", "--topic", "queues", "--source", "web", *same, "Fact one")
        self.refused(
            "knowledge", "add", "--topic", "queues", "--source", "web",
            "--url", "https://a.example.com/x", "--url", "https://b.example.org/x",
            "--url", "https://c.example.net/x", "Fact two",
        )  # fmt: skip
        self.assertFalse(os.path.exists(self.vp("knowledge/queues.md")))

    def test_registrable_domain_approximation(self) -> None:
        from juno.cmd.knowledge import registrable_domain as rd

        self.assertEqual(rd("https://a.b.example.com:8080/x"), "example.com")
        self.assertEqual(rd("https://www.bbc.co.uk/x"), "bbc.co.uk")
        self.assertEqual(rd("https://localhost/x"), "localhost")
        self.assertEqual(rd("http://10.0.0.1/x"), "10.0.0.1")

    def test_session_source_needs_evidence(self) -> None:
        self.refused("knowledge", "add", "--topic", "queues", "Jobs retry three times")
        self.refused(
            "knowledge",
            "add",
            "--topic",
            "queues",
            "--evidence",
            "invented words here ok",
            "Jobs retry thrice",
        )

    def test_file_cap(self) -> None:
        cap = writegate.FILE_LINES["knowledge"]
        self.put(
            "knowledge/big.md",
            "---\ntopic: big\n---\n\n" + "".join(f"- Fact {n:03d} here\n" for n in range(cap)),
        )
        out = self.refused(
            "knowledge", "add", "--topic", "big", "--evidence", QUOTE, "One fact too many"
        )
        self.assertIn("cap", out)


class Decision(Base):
    def test_needs_the_users_words_and_supersedes(self) -> None:
        self.refused(
            "decision", "--topic", "storage", "--evidence", "repo:app", "Use Postgres for storage"
        )
        self.ok("decision", "--topic", "storage", "--evidence", QUOTE, "Use Postgres for storage")
        text = self.get("decisions/storage.md")
        self.assertIn("## Current\n\n- Use Postgres for storage (user pid00001, ", text)
        self.ok(
            "decision",
            "--topic",
            "storage",
            "--evidence",
            QUOTE,
            "Use DynamoDB for storage",
            "--supersedes",
            "1",
        )
        text = self.get("decisions/storage.md")
        current = text.split("## Current")[1].split("## History")[0]
        self.assertIn("DynamoDB", current)
        self.assertNotIn("Postgres", current)
        self.assertIn("Postgres for storage (user pid00001", text.split("## History")[1])
        self.refused(
            "decision",
            "--topic",
            "storage",
            "--evidence",
            QUOTE,
            "Use X for storage",
            "--supersedes",
            "9",
        )


class Project(Base):
    def test_sections(self) -> None:
        self.touch_repo("app")
        self.ok(
            "project",
            "gotchas",
            "--repo",
            "app",
            "--evidence",
            "repo:app",
            "Migrations run on boot",
        )
        self.ok(
            "project", "run", "--repo", "app", "--evidence", "repo:app", "make dev starts the stack"
        )
        text = self.get("projects/app.md")
        self.assertIn("## Gotchas\n\n- Migrations run on boot", text)
        self.assertIn("## How to run\n\n- make dev starts the stack", text)
        self.assertIn("repo: app", text)

    def test_purpose_cap_and_repo_name(self) -> None:
        for n in range(3):
            self.ok(
                "project",
                "purpose",
                "--repo",
                "app",
                "--evidence",
                QUOTE,
                ("Serves partner invoices", "Syncs ledgers nightly", "Owned by billing team")[n],
            )
        self.assertIn(
            "Purpose already has",
            self.refused(
                "project",
                "purpose",
                "--repo",
                "app",
                "--evidence",
                QUOTE,
                "A completely other purpose",
            ),
        )
        self.refused(
            "project", "gotchas", "--repo", "../x", "--evidence", QUOTE, "Some gotcha text"
        )


class Procedure(Base):
    def test_create_update_and_undo_restores_the_previous_version(self) -> None:
        args = ("procedure", "--slug", "release", "--title", "Release the app", "--evidence", QUOTE)
        first = self.ok(*args, "Tag the commit", "Push the tag")
        before = self.get("procedures/release.md")
        self.assertIn("1. Tag the commit\n2. Push the tag", before)
        second = self.ok(*args, "Tag the commit", "Push the tag", "Watch the pipeline")
        self.assertIn("3. Watch the pipeline", self.get("procedures/release.md"))
        self.ok("undo", self.wid(second))
        self.assertEqual(self.get("procedures/release.md"), before)
        self.ok("undo", self.wid(first))
        self.assertFalse(os.path.exists(self.vp("procedures/release.md")))

    def test_step_count(self) -> None:
        self.refused(
            "procedure",
            "--slug",
            "release",
            "--title",
            "Release the app",
            "--evidence",
            QUOTE,
            "Only one step",
        )


class Idea(Base):
    def test_create_then_update(self) -> None:
        self.ok(
            "idea",
            "--slug",
            "faster-search",
            "--tags",
            "search",
            "Make the search index incremental",
        )
        text = self.get("ideas/faster-search.md")
        self.assertIn("status: planned", text)
        self.assertIn("## Idea\nMake the search index incremental\n", text)
        self.refused("idea", "--slug", "faster-search")
        self.ok(
            "idea",
            "--slug",
            "faster-search",
            "--status",
            "done",
            "--outcome",
            "Shipped in the index rewrite",
        )
        text = self.get("ideas/faster-search.md")
        self.assertIn("status: done", text)
        self.assertIn("## Outcome\n\n- Shipped in the index rewrite", text)

    def test_new_idea_needs_text(self) -> None:
        self.refused("idea", "--slug", "no-text")


class Self(Base):
    def test_quote_only(self) -> None:
        self.ok(
            "self",
            "--file",
            "values",
            "--evidence",
            QUOTE,
            "Prefers the first typed words over a summary",
        )
        line = self.get("self/values.md").splitlines()[-1]
        self.assertIn(f'src: user pid00001 · "{QUOTE}"', line)
        self.refused(
            "self", "--file", "values", "--evidence", "repo:app", "Likes a different thing"
        )


class Card(Base):
    def test_writes_to_the_routed_file_and_replaces_its_own(self) -> None:
        self.touch_repo("app")
        args = ("card", "--label", "export fix", "--learned", "none")
        out = self.ok(*args, "Fixed the export timeout.", "Stopped before the deploy.")
        text = self.get("handoffs/app.md")
        self.assertRegex(text, rf"## \d{{4}}-\d\d-\d\d \d\d:\d\d · - · export fix · {self.sid[:8]}")
        self.assertIn("Learned: none", text)
        self.assertIn("write w-", out)
        self.ok(*args, "Fixed the export timeout.", "Deployed it as well.")
        text = self.get("handoffs/app.md")
        self.assertEqual(text.count(f"· {self.sid[:8]}"), 1)
        self.assertIn("Deployed it as well.", text)
        card_events = [e for e in self.events() if e.get("card")]
        self.assertTrue(card_events)

    def test_replaces_a_stub(self) -> None:
        self.touch_repo("app")
        stub = f"## 2026-11-07 09:00 · - · stub · {self.sid[:8]}\nauto: true\nRepos: app\nLearned: none\n"
        self.put("handoffs/app.md", "# Handoffs · app\n\n" + stub)
        self.ok("card", "--learned", "none", "Did the work.", "Next is the deploy.")
        text = self.get("handoffs/app.md")
        self.assertNotIn("auto: true", text)
        self.assertEqual(text.count("\n## "), 1)

    def test_line_count_and_secrets(self) -> None:
        self.refused("card", "--learned", "none", "Only one line")
        self.refused("card", "--learned", "none", "a", "b", "c", "d", "e", "f")
        self.refused("card", "--learned", SECRET + " token", "Did the work.", "Next is the deploy.")

    def test_card_format_rules_shared_with_the_card_run(self) -> None:
        self.refused("card", "--learned", "none", "You fixed the export.", "Next is the deploy.")
        self.refused("card", "--learned", "none", "## Not a heading", "Next is the deploy.")


class FixForgetUndo(Base):
    def seed(self) -> str:
        self.ok("capture", "Look into the flaky invoice export test")
        return "inbox.md:2"

    def test_fix_replaces_the_line(self) -> None:
        ref = self.seed()
        out = self.ok(
            "fix", ref, "--was", "flaky invoice", "--evidence", QUOTE,
            "Look into the flaky invoice export job · 2026-11-07",
        )  # fmt: skip
        self.assertIn("Look into the flaky invoice export job", self.get("inbox.md"))
        self.ok("undo", self.wid(out))
        self.assertIn("export test", self.get("inbox.md"))

    def test_fix_checks_the_old_line_and_the_target(self) -> None:
        ref = self.seed()
        self.refused(
            "fix", ref, "--was", "not in the line at all", "--evidence", QUOTE, "A new line text"
        )
        self.refused(
            "fix", "inbox.md:99", "--was", "flaky invoice", "--evidence", QUOTE, "A new line text"
        )
        self.refused(
            "fix", "inbox.md:1", "--was", "# Inbox", "--evidence", QUOTE, "A new line text"
        )
        self.refused(
            "fix",
            "rules/global.md:1",
            "--was",
            "anything here",
            "--evidence",
            QUOTE,
            "A new line text",
        )
        self.refused(
            "fix",
            "../outside.md:1",
            "--was",
            "anything here",
            "--evidence",
            QUOTE,
            "A new line text",
        )

    def test_forget_archives_and_undo_restores_both_files(self) -> None:
        ref = self.seed()
        before = self.get("inbox.md")
        out = self.ok("forget", ref, "--was", "flaky invoice", "--evidence", QUOTE)
        self.assertNotIn("flaky", self.get("inbox.md"))
        archive = [n for n in os.listdir(self.vp("archive/decayed")) if n.startswith("forgotten-")]
        self.assertIn("flaky invoice", self.get("archive/decayed/" + archive[0]))
        self.ok("undo", self.wid(out))
        self.assertEqual(self.get("inbox.md"), before)
        self.assertFalse(os.path.exists(self.vp("archive/decayed/" + archive[0])))

    def test_undo_a_created_file_removes_it(self) -> None:
        out = self.ok("note", "--slug", "gone-soon", NOTE)
        self.ok("undo", self.wid(out))
        self.assertEqual(self.notes(), [])

    def test_undo_is_refused_when_a_later_write_touched_the_same_lines(self) -> None:
        self.refused("undo", "w-00000000")
        self.refused("undo", "nonsense")
        first = self.ok("capture", "Look into the flaky invoice export test")
        self.ok(
            "fix",
            "inbox.md:2",
            "--was",
            "flaky invoice",
            "--evidence",
            QUOTE,
            "Look into the flaky invoice export job",
        )
        out = self.refused("undo", self.wid(first))
        self.assertIn("later write", out)
        self.assertIn("snapshot", out)

    def test_undo_works_past_an_unrelated_later_write(self) -> None:
        first = self.ok("capture", "Look into the flaky invoice export test")
        self.ok("capture", "Rename the staging bucket before the audit")
        self.ok("undo", self.wid(first))
        text = self.get("inbox.md")
        self.assertNotIn("flaky", text)
        self.assertIn("staging bucket", text)

    def test_undo_twice_and_after_out_of_band_edit(self) -> None:
        out = self.ok("capture", "Look into the flaky invoice export test")
        self.ok("undo", self.wid(out))
        self.assertIn("already undone", self.refused("undo", self.wid(out)))
        out = self.ok("capture", "Look into the flaky invoice export test")
        self.put("inbox.md", "# Inbox\n\nhand edited\n")
        self.assertIn("cannot be undone", self.refused("undo", self.wid(out)))

    def test_undo_pointer_names_the_last_snapshot(self) -> None:
        with open(os.path.join(self.sb.home, ".gitconfig"), "w", encoding="utf-8") as handle:
            handle.write(
                "[user]\n\tname = T\n\temail = t@example.com\n[commit]\n\tgpgsign = false\n"
            )
        self.assertEqual(self.juno("snapshot")[0], 0)
        first = self.ok("capture", "Look into the flaky invoice export test")
        self.put("inbox.md", "# Inbox\n\nhand edited\n")
        self.assertRegex(self.refused("undo", self.wid(first)), r"last snapshot is [0-9a-f]{7,}")


class Caps(Base):
    def test_per_run_cap_counts_live_writes_only(self) -> None:
        ids = [
            self.wid(self.ok("capture", f"Distinct loose thought {chr(97 + n)}{chr(110 + n)}"))
            for n in range(5)
        ]
        records = writelog.read(self.cfg)
        self.assertEqual([r["id"] for r in records], ids)
        self.assertEqual(writelog.live_count(records, self.sid), 5)
        small = {r["id"]: r for r in records}
        self.assertTrue(all(small[i]["sid"] == self.sid for i in ids))
        for n in range(writegate.RUN_MAX - 5):
            writelog.append(self.cfg, {"id": f"w-{n:08x}", "sid": self.sid, "verb": "note"})
        self.assertIn("vault writes", self.refused("capture", "Yet another loose thought here"))


class WriteLog(Base):
    def test_record_carries_write_id_and_segment_id(self) -> None:
        out = self.ok("capture", "Look into the flaky invoice export test")
        rec = writelog.read(self.cfg)[0]
        self.assertEqual(rec["id"], self.wid(out))
        self.assertEqual(rec["sid"], self.sid)
        self.assertEqual(rec["seg"], self.sid[:8])
        self.assertEqual(rec["file"], "inbox.md")
        ledger = [e for e in self.events() if e["ev"] == "vault_write"]
        self.assertEqual(ledger[0]["id"], rec["id"])
        self.assertTrue(json.dumps(rec))

    def test_symlinked_target_is_refused(self) -> None:
        outside = os.path.join(self.sb.root, "elsewhere")
        os.makedirs(outside)
        shutil.rmtree(self.vp("notes"))
        os.symlink(outside, self.vp("notes"))
        self.refused("note", NOTE)
        self.assertEqual(os.listdir(outside), [])


class Registry(unittest.TestCase):
    def test_every_verb_is_registered(self) -> None:
        from juno import cli

        for verb in (
            "card knowledge note decision project procedure pitfall idea capture self "
            "fix forget undo"
        ).split():
            self.assertIn(verb, cli.VERBS)


if __name__ == "__main__":
    unittest.main()
