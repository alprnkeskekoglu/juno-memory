import contextlib
import io
import json
import time
import unittest

from juno import cli, config
from juno.adapters import ledgerio
from juno.core import routing, taskfile, vaultfmt
from tests.support.hooks import SID, HookCase


class TaskCase(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.make_repo("alpha", "feature/ABC-100-x")
        self.hook("session_start", self.payload("SessionStart", cwd=self.repo, source="startup"))
        self.session = dict(self.env, CLAUDE_CODE_SESSION_ID=SID)

    def juno(self, *argv: str, env: dict[str, str] | None = None) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(list(argv), self.session if env is None else env)
        return code, buffer.getvalue()

    def vault_has(self, rel: str) -> bool:
        try:
            self.read(rel)
        except FileNotFoundError:
            return False
        return True

    def cfg(self) -> config.Config:
        return config.load(self.session)

    def fields(self, key: str) -> vaultfmt.Fields:
        return vaultfmt.parse_frontmatter(self.read(f"tasks/{key}.md"))[0]


class TaskSet(TaskCase):
    def test_creates_the_file_with_frontmatter(self) -> None:
        code, out = self.juno(
            "task", "set", "ABC-100", "--title", "Fix the thing", "--status", "In Review"
        )
        self.assertEqual(code, 0, out)
        fields = self.fields("ABC-100")
        self.assertEqual(fields["jira"], "ABC-100")
        self.assertEqual(fields["title"], "Fix the thing")
        self.assertEqual(fields["status"], "in-review")
        self.assertEqual(fields["updated"], time.strftime("%Y-%m-%d"))
        self.assertEqual(fields["created"], fields["updated"])
        self.assertIn("## Notes", self.read("tasks/ABC-100.md"))
        self.assertNotIn("url", fields)

    def test_url_defaults_from_base_url(self) -> None:
        with open(self.sb.config, encoding="utf-8") as handle:
            text = handle.read()
        with open(self.sb.config, "w", encoding="utf-8") as handle:
            handle.write(text.replace("[jira]", '[jira]\nbase_url = "https://jira.example.test/"'))
        code, out = self.juno("task", "set", "abc-100", "--title", "T", "--status", "open")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.fields("ABC-100")["url"], "https://jira.example.test/browse/ABC-100")

    def test_explicit_url_and_repos(self) -> None:
        code, out = self.juno(
            "task", "set", "ABC-100", "--title", "T", "--status", "open",
            "--url", "https://x.test/ABC-100", "--repo", "alpha", "--repo", "beta",
        )  # fmt: skip
        self.assertEqual(code, 0, out)
        fields = self.fields("ABC-100")
        self.assertEqual(fields["url"], "https://x.test/ABC-100")
        self.assertEqual(fields["repos"], ["alpha", "beta"])

    def test_update_keeps_body_other_fields_and_created(self) -> None:
        self.write(
            "tasks/ABC-100.md",
            "---\njira: ABC-100\ntitle: Old\nstatus: planned\nrepos: [alpha]\n"
            "created: 2026-01-01\nupdated: 2026-01-02\nowner: me\n---\n\n"
            "## Decisions\n- keep this\n\n## Notes\n- and this\n",
        )
        code, out = self.juno("task", "set", "ABC-100", "--status", "Done")
        self.assertEqual(code, 0, out)
        text = self.read("tasks/ABC-100.md")
        fields = self.fields("ABC-100")
        self.assertEqual(fields["status"], "done")
        self.assertEqual(fields["title"], "Old")
        self.assertEqual(fields["created"], "2026-01-01")
        self.assertEqual(fields["owner"], "me")
        self.assertEqual(fields["repos"], ["alpha"])
        self.assertEqual(fields["updated"], time.strftime("%Y-%m-%d"))
        self.assertIn("- keep this", text)
        self.assertIn("- and this", text)
        self.assertEqual(text.count("status:"), 1)

    def test_awkward_title_round_trips(self) -> None:
        title = 'Fix: "quoted" # thing [x]'
        self.assertEqual(
            self.juno("task", "set", "ABC-100", "--title", title, "--status", "open")[0], 0
        )
        self.assertEqual(self.fields("ABC-100")["title"], "Fix: 'quoted' # thing [x]")

    def test_second_call_is_stable(self) -> None:
        args = ("task", "set", "ABC-100", "--title", "T", "--status", "open")
        self.juno(*args)
        first = self.read("tasks/ABC-100.md")
        self.juno(*args)
        self.assertEqual(self.read("tasks/ABC-100.md"), first)

    def test_new_file_needs_title_and_status(self) -> None:
        code, out = self.juno("task", "set", "ABC-100", "--title", "T")
        self.assertEqual(code, 2)
        self.assertIn("--title and --status", out)

    def test_empty_update_is_refused(self) -> None:
        self.juno("task", "set", "ABC-100", "--title", "T", "--status", "open")
        self.assertEqual(self.juno("task", "set", "ABC-100")[0], 2)

    def test_bad_values_are_refused(self) -> None:
        base = ("task", "set", "ABC-100", "--title", "T", "--status", "open")
        for extra in (
            ("--status", "bad;status"),
            ("--url", "javascript:alert(1)"),
            ("--repo", "../x"),
            ("--title", "token = ghp_" + "a" * 36),
        ):
            with self.subTest(extra):
                self.assertEqual(self.juno(*base, *extra)[0], 2)
        self.assertFalse(self.vault_has("tasks/ABC-100.md"))

    def test_key_must_match_prefixes(self) -> None:
        for key in ("XYZ-1", "ABC-", "ABC-1 x", "../ABC-1"):
            with self.subTest(key):
                self.assertEqual(
                    self.juno("task", "set", key, "--title", "T", "--status", "o")[0], 2
                )

    def test_write_refusals(self) -> None:
        args = ("task", "set", "ABC-100", "--title", "T", "--status", "open")
        cases = {
            "not interactive": {
                k: v for k, v in self.session.items() if k != "JUNO_TEST_INTERACTIVE"
            },
            "agent": dict(self.session, CLAUDE_CODE_AGENT_ID="a1"),
            "no session": self.env,
            "unknown session": dict(self.env, CLAUDE_CODE_SESSION_ID="nope"),
        }
        for name, env in cases.items():
            with self.subTest(name):
                code, out = self.juno(*args, env=dict(env, CLAUDE_PID="1"))
                self.assertEqual(code, 2)
                self.assertIn("Juno: refused:", out)
        self.assertFalse(self.vault_has("tasks/ABC-100.md"))

    def test_nosave_refuses(self) -> None:
        self.hook("user_prompt_submit", self.payload("UserPromptSubmit", prompt="hello [nosave]"))
        code, out = self.juno("task", "set", "ABC-100", "--title", "T", "--status", "open")
        self.assertEqual(code, 2)
        self.assertIn("nosave", out)


class TaskRead(TaskCase):
    def setUp(self) -> None:
        super().setUp()
        for key, status, repo in (("ABC-100", "open", "alpha"), ("ABC-101", "done", "beta")):
            self.juno(
                "task", "set", key, "--title", f"title {key}", "--status", status, "--repo", repo
            )

    def test_show(self) -> None:
        code, out = self.juno("task", "show", "abc-100")
        self.assertEqual(code, 0)
        self.assertIn("title: title ABC-100", out)
        code, out = self.juno("task", "show", "ABC-100", "--json")
        self.assertEqual(json.loads(out)["status"], "open")
        self.assertEqual(self.juno("task", "show", "ABC-999")[0], 2)

    def test_tasks_lists_and_filters(self) -> None:
        code, out = self.juno("tasks")
        self.assertEqual(code, 0)
        self.assertEqual(
            out.splitlines(),
            ["ABC-100 · open · title ABC-100 · alpha", "ABC-101 · done · title ABC-101 · beta"],
        )
        self.assertEqual(len(self.juno("tasks", "--status", "Done")[1].splitlines()), 1)
        self.assertIn("ABC-100", self.juno("tasks", "--repo", "ALPHA")[1])
        self.assertEqual(self.juno("tasks", "--repo", "gamma")[1], "no tasks\n")
        data = json.loads(self.juno("tasks", "--json")[1])
        self.assertEqual([t["key"] for t in data["tasks"]], ["ABC-100", "ABC-101"])


class WithoutJira(TaskCase):
    TICKETS = False

    def test_verbs_say_not_configured(self) -> None:
        self.assertEqual(self.juno("tasks"), (0, "jira not configured\n"))
        for argv in (
            ("task", "show", "ABC-100"),
            ("task", "set", "ABC-100", "--title", "T", "--status", "o"),
        ):
            code, out = self.juno(*argv)
            self.assertEqual(code, 2)
            self.assertIn("jira not configured", out)
        self.assertFalse(self.vault_has("tasks/ABC-100.md"))

    def test_branch_ticket_is_not_detected_and_card_routes_by_repo(self) -> None:
        self.assertEqual(self.ledger()[0]["repos"], [["alpha", None]])
        seg = ledgerio.fold(self.cfg(), SID)
        self.assertEqual(list(seg.tickets), [])
        self.assertIsNone(seg.current_ticket())
        targets = routing.handoff_targets(seg)
        self.assertEqual([t["path"] for t in targets], ["handoffs/alpha.md"])
        self.assertEqual(targets[0]["key"], "-")
        self.assertNotIn("Juno pack: ticket", self.start_text())

    def start_text(self) -> str:
        return self.context(
            self.hook(
                "session_start",
                self.payload(
                    "SessionStart", session_id="other-sid", cwd=self.repo, source="startup"
                ),
            )
        )


class WithJira(TaskCase):
    def test_branch_ticket_is_detected_and_routes_by_ticket(self) -> None:
        self.assertEqual(self.ledger()[0]["repos"], [["alpha", "ABC-100"]])
        seg = ledgerio.fold(self.cfg(), SID)
        self.assertEqual(seg.current_ticket(), "ABC-100")
        self.assertEqual(routing.handoff_targets(seg)[0]["path"], "handoffs/ABC-100.md")

    def start_text(self, sid: str) -> str:
        return self.context(
            self.hook(
                "session_start",
                self.payload("SessionStart", session_id=sid, cwd=self.repo, source="startup"),
            )
        )

    def test_missing_task_file_hint_then_none_once_filled(self) -> None:
        first = self.start_text("11111111-1111-4111-8111-111111111111")
        self.assertIn("=== Juno pack: ticket ABC-100 ===", first)
        self.assertIn('juno task set ABC-100 --title "<title>" --status "<status>"', first)
        self.juno("task", "set", "ABC-100", "--title", "Real title", "--status", "open")
        second = self.start_text("22222222-2222-4222-8222-222222222222")
        self.assertNotIn("juno task set", second)


class TaskFile(unittest.TestCase):
    def test_merge_adds_jira_when_missing(self) -> None:
        out = taskfile.merge(
            "---\ntitle: X\n---\nbody\n", "ABC-1", {"status": "open"}, "2026-01-01"
        )
        fields, body = vaultfmt.parse_frontmatter(out)
        self.assertEqual(fields["jira"], "ABC-1")
        self.assertEqual(fields["status"], "open")
        self.assertEqual(body.strip(), "body")

    def test_merge_without_frontmatter_keeps_text_as_body(self) -> None:
        out = taskfile.merge("## Notes\n- a\n", "ABC-1", {"title": "T"}, "2026-01-01")
        self.assertTrue(out.startswith("---\njira: ABC-1\ntitle: T\ncreated: 2026-01-01\n"))
        self.assertIn("- a", out)
        self.assertTrue(out.endswith("\n"))

    def test_clean_helpers(self) -> None:
        self.assertEqual(taskfile.clean_status(" In  Progress "), ("in-progress", None))
        self.assertIsNotNone(taskfile.clean_status("")[1])
        self.assertIsNotNone(taskfile.clean_title("   ")[1])
        self.assertEqual(taskfile.default_url("", "ABC-1"), "")
        self.assertEqual(taskfile.default_url("not a url", "ABC-1"), "")


if __name__ == "__main__":
    unittest.main()
