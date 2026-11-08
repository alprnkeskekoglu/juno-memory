import json
import os
import unittest

from juno.adapters import queues
from tests.support.write import WriteCase

QUOTE = "put the invoice export cleanup on my work list for next week"
BODY = (
    "## Task\nClean up the invoice export job\n"
    "## Why\nIt times out on large partners\nSecond reason line\n"
    "## Research\nLook at the batch size\n"
    "## First step\nProfile one export run\n"
)


class BacklogCase(WriteCase):
    TICKETS = True

    def setUp(self) -> None:
        super().setUp()
        self.prompt(QUOTE, "pid00002")

    def add(
        self, title: str = "Invoice export cleanup", *extra: str, body: str = BODY
    ) -> tuple[int, str]:
        return self.juno(
            "backlog",
            "add",
            "--quote",
            QUOTE,
            "--title",
            title,
            "--priority",
            "p2",
            "--body-file",
            self.body_file(body),
            *extra,
        )

    def item(self, slug: str = "invoice-export-cleanup") -> str:
        return self.get(f"backlog/{slug}.md")


class Add(BacklogCase):
    def test_add_writes_the_item(self) -> None:
        code, out = self.add("Invoice export cleanup", "--due", "2026-12-01", "--repo", "billing")
        self.assertEqual(code, 0, out)
        text = self.item()
        self.assertIn("title: Invoice export cleanup", text)
        self.assertIn("status: inbox\npriority: p2\ndue: 2026-12-01", text)
        self.assertIn("repos: [billing]", text)
        self.assertRegex(text, r'src: user pid00002 · "put the invoice export')
        self.assertIn("## Log\n- ", text)
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "body.md")))

    def test_quote_is_verified(self) -> None:
        code, out = self.juno(
            "backlog",
            "add",
            "--quote",
            "I never typed this long sentence anywhere",
            "--title",
            "Some title here",
            "--priority",
            "p1",
            "--body-file",
            self.body_file(BODY),
        )
        self.assertEqual(code, 2)
        self.assertIn("not a verbatim part", out)
        with self.assertRaises(SystemExit) as raised:
            self.juno("backlog", "add", "--title", "x", "--priority", "p1", "--body-file", "/x")
        self.assertEqual(raised.exception.code, 2)

    def test_duplicates_and_slug_clashes(self) -> None:
        self.assertEqual(self.add()[0], 0)
        code, out = self.add()
        self.assertEqual(code, 2)
        self.assertIn("same title", out)
        self.juno("backlog", "done", "--quote", QUOTE, "invoice-export-cleanup")
        self.assertEqual(self.add()[0], 0)
        self.assertTrue(os.path.exists(self.vp("backlog/invoice-export-cleanup-2.md")))

    def test_field_validation(self) -> None:
        self.assertEqual(self.add("Bad due", "--due", "tomorrow")[0], 2)
        self.assertEqual(self.add("Bad ticket", "--ticket", "nope")[0], 2)
        self.assertEqual(self.add("Bad repo", "--repo", "a b")[0], 2)
        self.assertEqual(self.add("Bad source", "--source", "ftp://x")[0], 2)
        self.assertEqual(self.add("#nope")[0], 2)
        self.assertEqual(self.add("Tiny")[0], 2)

    def test_tickets_need_jira_prefixes(self) -> None:
        self.assertEqual(self.add("Ticketed item", "--ticket", "abc-12")[0], 0)
        self.assertIn("tickets: [ABC-12]", self.get("backlog/ticketed-item.md"))

    def test_secret_and_private_data_refused(self) -> None:
        secret = BODY.replace("Profile one export run", "use token=abcdef1234567890abcdef")
        self.assertEqual(self.add(body=secret)[0], 2)
        mail = BODY.replace("Profile one export run", "mail jo@example.com about it")
        self.assertEqual(self.add(body=mail)[0], 2)
        url = BODY.replace(
            "Profile one export run", "see https://x.example/a/1234567890 for ABC-123456"
        )
        self.assertEqual(self.add("Url item here", body=url)[0], 0)

    def test_body_location_and_shape(self) -> None:
        outside = os.path.join(self.sb.root, "elsewhere.md")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write(BODY)
        code, out = self.juno(
            "backlog",
            "add",
            "--quote",
            QUOTE,
            "--title",
            "Outside body",
            "--priority",
            "p1",
            "--body-file",
            outside,
        )
        self.assertEqual(code, 2)
        self.assertIn("/tmp or the Juno state dir", out)
        self.assertEqual(self.add(body=BODY.replace("## Why", "## Reason"))[0], 2)
        self.assertEqual(self.add(body=BODY + "## Extra\nx\n")[0], 2)
        self.assertEqual(self.add(body=BODY.replace("Second reason line\n", "a\nb\nc\n"))[0], 2)

    def test_ticket_link_only_into_existing_task_files(self) -> None:
        self.put("tasks/ABC-12.md", "---\ntitle: T\nstatus: open\n---\n## Notes\n- existing\n")
        code, out = self.add("Linked item here", "--ticket", "ABC-12", "--ticket", "ABC-13")
        self.assertEqual(code, 0, out)
        self.assertIn("linked from: tasks/ABC-12.md", out)
        self.assertIn("- Backlog: [[backlog/linked-item-here]]", self.get("tasks/ABC-12.md"))
        self.assertFalse(os.path.exists(self.vp("tasks/ABC-13.md")))

    def test_refused_for_a_subagent_and_headless(self) -> None:
        for extra in (
            {"CLAUDE_CODE_AGENT_ID": "a1"},
            {"JUNO_TEST_INTERACTIVE": "", "CLAUDE_CODE_SESSION_ATTENDED": "0"},
        ):
            code, _ = self.juno(
                "backlog",
                "add",
                "--quote",
                QUOTE,
                "--title",
                "Blocked item",
                "--priority",
                "p1",
                "--body-file",
                self.body_file(BODY),
                env=extra,
            )
            self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.vp("backlog/blocked-item.md")))


class NoJira(WriteCase):
    def test_ticket_option_is_refused_without_prefixes(self) -> None:
        self.prompt(QUOTE, "pid00002")
        code, out = self.juno(
            "backlog",
            "add",
            "--quote",
            QUOTE,
            "--title",
            "Plain item here",
            "--priority",
            "p3",
            "--ticket",
            "ABC-1",
            "--body-file",
            self.body_file(BODY),
        )
        self.assertEqual((code, "need [jira] prefixes" in out), (2, True))


class Change(BacklogCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.add()[0], 0)

    def test_set_writes_fields_and_log_lines(self) -> None:
        code, out = self.juno(
            "backlog",
            "set",
            "--quote",
            QUOTE,
            "--status",
            "doing",
            "--priority",
            "p1",
            "--due",
            "2026-12-24",
            "--ticket",
            "ABC-5",
            "--repo",
            "billing",
            "invoice-export-cleanup",
        )
        self.assertEqual(code, 0, out)
        text = self.item()
        for needle in (
            "status: doing",
            "priority: p1",
            "due: 2026-12-24",
            "tickets: [ABC-5]",
            "repos: [billing]",
        ):
            self.assertIn(needle, text)
        self.assertIn("status inbox -> doing", text)
        self.assertEqual(
            self.juno(
                "backlog", "set", "--quote", QUOTE, "--status", "doing", "invoice-export-cleanup"
            )[1],
            "Juno: no change to backlog/invoice-export-cleanup.md.\n",
        )

    def test_untag_and_conflicts(self) -> None:
        self.juno("backlog", "set", "--quote", QUOTE, "--ticket", "ABC-5", "invoice-export-cleanup")
        code, _ = self.juno(
            "backlog", "set", "--quote", QUOTE, "--untag-ticket", "ABC-5", "invoice-export-cleanup"
        )
        self.assertEqual(code, 0)
        self.assertNotIn("tickets:", self.item())
        both = self.juno(
            "backlog",
            "set",
            "--quote",
            QUOTE,
            "--ticket",
            "ABC-5",
            "--untag-ticket",
            "ABC-5",
            "invoice-export-cleanup",
        )
        self.assertEqual(both[0], 2)

    def test_done_and_drop_and_unknown_slug(self) -> None:
        self.assertEqual(
            self.juno("backlog", "done", "--quote", QUOTE, "invoice-export-cleanup")[0], 0
        )
        self.assertIn("status: done", self.item())
        code, out = self.juno(
            "backlog",
            "drop",
            "--quote",
            QUOTE,
            "--reason",
            "no longer needed",
            "invoice-export-cleanup",
        )
        self.assertEqual(code, 0, out)
        self.assertIn("(no longer needed)", self.item())
        self.assertEqual(self.juno("backlog", "done", "--quote", QUOTE, "no-such-item")[0], 2)

    def test_change_needs_a_verified_quote(self) -> None:
        code, out = self.juno(
            "backlog",
            "done",
            "--quote",
            "this was never typed in the session at all",
            "invoice-export-cleanup",
        )
        self.assertEqual((code, "not a verbatim part" in out), (2, True))

    def test_log_needs_no_quote(self) -> None:
        code, out = self.juno(
            "backlog", "log", "invoice-export-cleanup", "Profiled the job, batch size is the cause"
        )
        self.assertEqual(code, 0, out)
        self.assertIn("Profiled the job", self.item())
        self.assertEqual(self.juno("backlog", "log", "invoice-export-cleanup", "short")[0], 2)

    def test_add_to_and_edit_sections(self) -> None:
        extra = self.body_file("Another research line\n", "extra.md")
        code, out = self.juno(
            "backlog",
            "add-to",
            "invoice-export-cleanup",
            "--quote",
            QUOTE,
            "--section",
            "research",
            "--body-file",
            extra,
        )
        self.assertEqual(code, 0, out)
        self.assertIn("Another research line", self.item())
        shown = self.juno("backlog", "show", "--numbered", "invoice-export-cleanup")[1]
        self.assertIn("2: Another research line", shown)
        repl = self.body_file("Replaced research line\n", "repl.md")
        code, out = self.juno(
            "backlog",
            "edit",
            "invoice-export-cleanup",
            "--quote",
            QUOTE,
            "--section",
            "research",
            "--line",
            "2",
            "--replace-file",
            repl,
        )
        self.assertEqual(code, 0, out)
        self.assertIn("Replaced research line", self.item())
        code, out = self.juno(
            "backlog",
            "edit",
            "invoice-export-cleanup",
            "--quote",
            QUOTE,
            "--section",
            "research",
            "--line",
            "2",
            "--delete",
        )
        self.assertEqual(code, 0, out)
        self.assertNotIn("Replaced research line", self.item())
        refused = self.juno(
            "backlog",
            "edit",
            "invoice-export-cleanup",
            "--quote",
            QUOTE,
            "--section",
            "task",
            "--line",
            "1",
            "--delete",
        )
        self.assertEqual(refused[0], 2)
        self.assertIn("would be empty", refused[1])


class Read(BacklogCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.add("Alpha item here")[0], 0)
        self.assertEqual(self.add("Beta item here", "--due", "2020-01-01")[0], 0)
        self.juno("backlog", "set", "--quote", QUOTE, "--priority", "p1", "beta-item-here")
        self.juno("backlog", "done", "--quote", QUOTE, "alpha-item-here")

    def test_list_orders_filters_and_hides_closed(self) -> None:
        out = self.juno("backlog", "list")[1]
        self.assertIn("beta-item-here · inbox · p1 · due 2020-01-01 (overdue)", out)
        self.assertNotIn("alpha-item-here", out)
        self.assertIn("alpha-item-here · done", self.juno("backlog", "list", "--all")[1])
        self.assertIn("no backlog items", self.juno("backlog", "list", "--priority", "p3")[1])

    def test_json_shape_and_invalid_files(self) -> None:
        self.put("backlog/broken-item.md", "no frontmatter\n")
        data = json.loads(self.juno("backlog", "list", "--json")[1])
        self.assertEqual([i["slug"] for i in data["items"]], ["beta-item-here"])
        self.assertEqual(data["invalid"], ["broken-item"])
        self.assertEqual(data["suggestions"], [])

    def test_subagent_may_list_and_show_only(self) -> None:
        agent = {"CLAUDE_CODE_AGENT_ID": "a1"}
        self.assertEqual(self.juno("backlog", "list", env=agent)[0], 0)
        self.assertEqual(self.juno("backlog", "show", "beta-item-here", env=agent)[0], 0)
        self.assertEqual(
            self.juno("backlog", "log", "beta-item-here", "ten chars or more here", env=agent)[0], 2
        )

    def test_symlinked_item_is_not_read(self) -> None:
        target = os.path.join(self.sb.root, "outside.md")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(self.item("beta-item-here"))
        os.symlink(target, self.vp("backlog/linked-item.md"))
        data = json.loads(self.juno("backlog", "list", "--json")[1])
        self.assertIn("linked-item", data["invalid"])
        self.assertEqual(self.juno("backlog", "show", "linked-item")[0], 2)


class Suggestions(BacklogCase):
    SUG = "b123abc"

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.add()[0], 0)
        self.seed_queue(
            queues.BACKLOG,
            [
                {
                    "id": self.SUG,
                    "slug": "invoice-export-cleanup",
                    "log": "Merged the batch size fix in the billing repo",
                    "new_status": "done",
                    "seg8": "57fb7685",
                    "status": "pending",
                    "t": 1.0,
                }
            ],
        )

    def test_list_shows_pending_suggestions(self) -> None:
        out = self.juno("backlog", "list")[1]
        self.assertIn(f"{self.SUG} · invoice-export-cleanup · status -> done", out)

    def test_accept_needs_a_chosen_id_then_applies(self) -> None:
        code, out = self.juno("backlog", "accept", self.SUG)
        self.assertEqual((code, "did not choose" in out), (2, True))
        self.prompt(f"accept {self.SUG}", "pid00003")
        code, out = self.juno("backlog", "accept", self.SUG)
        self.assertEqual(code, 0, out)
        self.assertIn("status: done", self.item())
        self.assertIn("Merged the batch size fix", self.item())
        self.assertEqual(self.queue_status(queues.BACKLOG), {self.SUG: "accepted"})

    def test_reject(self) -> None:
        code, out = self.juno("backlog", "reject", self.SUG)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.queue_status(queues.BACKLOG), {self.SUG: "rejected"})
        self.assertEqual(self.juno("backlog", "reject", self.SUG)[0], 2)
        self.assertNotIn(self.SUG, self.juno("backlog", "list")[1])


if __name__ == "__main__":
    unittest.main()
