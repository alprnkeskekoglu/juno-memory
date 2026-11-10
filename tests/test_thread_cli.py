import json
import re
import time
import unittest

from juno.adapters import queues
from tests.support.write import WriteCase

LINE = "- [app] Decide on the retry policy for the sync job · since 2026-10-01"
LINE2 = "- [ABC-7] Waiting for the partner answer on rollout · since 2026-10-02"
THREADS = f"# Threads\n\n{LINE}\n{LINE2}\n"


class Add(WriteCase):
    def test_add_and_list(self) -> None:
        code, out = self.juno(
            "thread", "add", "--tag", "app", "Decide on the retry policy for sync"
        )
        self.assertEqual(code, 0, out)
        text = self.get("threads.md")
        self.assertRegex(
            text, r"- \[app\] Decide on the retry policy for sync · since \d{4}-\d\d-\d\d\n"
        )
        listed = json.loads(self.juno("thread", "list", "--json")[1])["threads"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(
            self.juno("thread", "add", "--tag", "app", "Decide on the retry policy for sync")[0], 0
        )
        self.assertEqual(self.get("threads.md").count("retry policy"), 1)

    def test_multiple_tags_and_validation(self) -> None:
        self.assertEqual(
            self.juno(
                "thread", "add", "--tag", "app", "--tag", "ABC-9", "A long enough open question"
            )[0],
            0,
        )
        self.assertIn("- [app, ABC-9] A long", self.get("threads.md"))
        self.assertEqual(
            self.juno("thread", "add", "--tag", "a b", "A long enough open question")[0], 2
        )
        self.assertEqual(self.juno("thread", "add", "--tag", "app", "short")[0], 2)
        self.assertEqual(
            self.juno("thread", "add", "--tag", "app", "has a · separator in it")[0], 2
        )
        self.assertEqual(
            self.juno("thread", "add", "--tag", "app", "mail jo@example.com about it")[0], 2
        )
        self.assertEqual(
            self.juno("thread", "add", "--tag", "app", "text with [bracket] inside")[0], 2
        )

    def test_per_tag_cap(self) -> None:
        for n in range(5):
            self.assertEqual(
                self.juno("thread", "add", "--tag", "app", f"Open question number {n} here")[0], 0
            )
        code, out = self.juno("thread", "add", "--tag", "app", "One more open question here")
        self.assertEqual((code, "already has 5" in out), (2, True))

    def test_refused_in_a_subagent(self) -> None:
        code, _ = self.juno(
            "thread",
            "add",
            "--tag",
            "app",
            "A long enough open question",
            env={"CLAUDE_CODE_AGENT_ID": "a"},
        )
        self.assertEqual(code, 2)
        self.assertEqual(self.get("threads.md").count("- ["), 0)


OLD_CAP = (
    "The vault's protocol/vault-files.md still shows the old cap of 30 and lacks the cap "
    "command; the update path (maybe juno migrate) is unchecked."
)
NEW_CAP = (
    "The vault protocol/vault-files.md still shows the old cap text (limit 30); "
    "the update path is unchecked."
)
PLUGIN_CACHE = (
    "Plugin cache is still 1.0.3, so the card masking and auto ticket counting fixes need a "
    "plugin update and a new session; vault snapshot also pending."
)
LOST_FACTS = (
    "Two verified facts (claude setup-token OAuth token, Neal Mohan 2026 letter) are not yet "
    "in the vault because the write cap was reached."
)


class Stale(WriteCase):
    def setUp(self) -> None:
        super().setUp()
        day = 86400
        stamps = [
            time.strftime("%Y-%m-%d", time.localtime(time.time() - n * day)) for n in (30, 8, 2)
        ]
        self.put(
            "threads.md",
            "# Threads\n\n"
            + "".join(
                f"- [app] Open question number {n} here · since {d}\n"
                for n, d in enumerate(stamps)
            ),
        )

    def listed(self, *extra: str) -> list[str]:
        code, out = self.juno("thread", "list", "--stale", "--json", *extra)
        self.assertEqual(code, 0, out)
        return json.loads(out)["threads"]

    def test_default_is_seven_days_and_read_only(self) -> None:
        before = self.get("threads.md")
        rows = self.listed()
        self.assertEqual(len(rows), 2)
        self.assertIn("number 0", rows[0])
        self.assertIn("number 1", rows[1])
        self.assertEqual(self.get("threads.md"), before)

    def test_days_option_and_plain_output(self) -> None:
        self.assertEqual(len(self.listed("--days", "20")), 1)
        self.assertEqual(len(self.listed("--days", "1")), 3)
        code, out = self.juno("thread", "list", "--stale")
        self.assertEqual((code, out.count("- [app]")), (0, 2))
        self.put("threads.md", "# Threads\n")
        self.assertIn("no stale", self.juno("thread", "list", "--stale")[1])


class NearDuplicate(WriteCase):
    def test_near_duplicate_updates_the_existing_line(self) -> None:
        self.put("threads.md", f"# Threads\n\n- [juno-memory] {OLD_CAP} · since 2026-11-10\n")
        code, out = self.juno("thread", "add", "--tag", "juno-memory", NEW_CAP)
        self.assertEqual(code, 0, out)
        rows = [x for x in self.get("threads.md").splitlines() if x.startswith("- ")]
        self.assertEqual(rows, [f"- [juno-memory] {NEW_CAP} · since 2026-11-10"])
        self.assertIn("updated", out)

    def test_distinct_threads_do_not_merge(self) -> None:
        self.put("threads.md", f"# Threads\n\n- [juno-memory] {PLUGIN_CACHE} · since 2026-11-09\n")
        for text in (OLD_CAP, LOST_FACTS):
            self.assertEqual(self.juno("thread", "add", "--tag", "juno-memory", text)[0], 0)
        rows = [x for x in self.get("threads.md").splitlines() if x.startswith("- ")]
        self.assertEqual(len(rows), 3)


class Candidates(WriteCase):
    A = "t111111"
    B = "t222222"

    def setUp(self) -> None:
        super().setUp()
        self.put("threads.md", THREADS)
        self.seed_queue(
            queues.THREADS,
            [
                {
                    "id": self.A,
                    "line": LINE,
                    "signals": ["PR app#4 merged"],
                    "t": 1.0,
                    "status": "pending",
                },
                {
                    "id": self.B,
                    "line": LINE2,
                    "signals": ["ticket ABC-7 is done"],
                    "t": 1.0,
                    "status": "pending",
                },
            ],
        )

    def test_pending_lists_both(self) -> None:
        out = self.juno("thread", "pending")[1]
        self.assertIn(self.A, out)
        self.assertIn("PR app#4 merged", out)
        self.assertEqual(len(json.loads(self.juno("thread", "pending", "--json")[1])), 2)

    def test_close_needs_a_chosen_id_and_removes_the_line(self) -> None:
        code, out = self.juno("thread", "close", self.A)
        self.assertEqual((code, "did not choose" in out), (2, True))
        self.answer(f"{self.A} · close the retry policy thread")
        code, out = self.juno("thread", "close", self.A)
        self.assertEqual(code, 0, out)
        self.assertNotIn("retry policy", self.get("threads.md"))
        self.assertIn("partner answer", self.get("threads.md"))
        self.assertEqual(self.queue_status(queues.THREADS)[self.A], "closed")

    def test_line_edited_meanwhile_is_reported_gone(self) -> None:
        self.put("threads.md", THREADS.replace("retry policy", "retry rules"))
        self.prompt(f"close {self.A}", "pid00002")
        code, out = self.juno("thread", "close", self.A)
        self.assertEqual(code, 0, out)
        self.assertIn("no longer in threads.md", out)
        self.assertEqual(self.queue_status(queues.THREADS)[self.A], "gone")

    def test_keep_and_unknown_ids(self) -> None:
        code, out = self.juno("thread", "keep", self.A, self.B)
        self.assertEqual(code, 0, out)
        self.assertEqual(set(self.queue_status(queues.THREADS).values()), {"kept"})
        self.assertEqual(self.juno("thread", "keep", self.A)[0], 2)
        self.assertEqual(self.juno("thread", "close", "bad")[0], 2)

    def test_close_refused_in_a_subagent(self) -> None:
        self.prompt(f"close {self.A}", "pid00002")
        code, _ = self.juno("thread", "close", self.A, env={"CLAUDE_CODE_AGENT_ID": "a"})
        self.assertEqual(code, 2)
        self.assertIn("retry policy", self.get("threads.md"))


class DirectClose(WriteCase):
    QUOTE = "please close the retry policy thread now"

    def setUp(self) -> None:
        super().setUp()
        self.put("threads.md", THREADS)
        self.prompt(self.QUOTE, "pid00002")

    def close(self, *extra: str, line: str = "3", was: str = "retry policy") -> tuple[int, str]:
        return self.juno("thread", "close", "--line", line, "--was", was, *extra)

    def test_closes_with_a_quote_and_undo_restores(self) -> None:
        code, out = self.close("--evidence", self.QUOTE)
        self.assertEqual(code, 0, out)
        self.assertNotIn("retry policy", self.get("threads.md"))
        self.assertIn("partner answer", self.get("threads.md"))
        self.assertTrue(any(e.get("ev") == "thread_closed" for e in self.events()))
        match = re.search(r"juno undo (w-[0-9a-f]+)", out)
        assert match
        code, out = self.juno("undo", match.group(1))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.get("threads.md"), THREADS)

    def test_refuses_without_a_verbatim_quote(self) -> None:
        for evidence in ("close it please, this was never typed", "too short"):
            code, out = self.close("--evidence", evidence)
            self.assertEqual(code, 2, out)
        self.assertEqual(self.get("threads.md"), THREADS)

    def test_refuses_repo_and_ticket_anchors(self) -> None:
        self.assertEqual(self.close("--evidence", "repo:app")[0], 2)
        self.assertEqual(self.close("--evidence", "ticket:ABC-7")[0], 2)
        self.assertEqual(self.get("threads.md"), THREADS)

    def test_wrong_was_or_line(self) -> None:
        code, out = self.close("--evidence", self.QUOTE, was="something unrelated")
        self.assertEqual((code, "does not contain --was" in out), (2, True))
        self.assertEqual(self.close("--evidence", self.QUOTE, line="99")[0], 2)
        self.assertEqual(self.get("threads.md"), THREADS)

    def test_line_must_be_a_thread_line(self) -> None:
        code, out = self.close("--evidence", self.QUOTE, line="1", was="# Threads")
        self.assertEqual(code, 2, out)
        self.put("threads.md", THREADS + "- plain bullet that is not a thread line\n")
        code, out = self.close("--evidence", self.QUOTE, line="5", was="plain bullet that")
        self.assertEqual((code, "not a thread line" in out), (2, True))

    def test_refused_in_a_subagent(self) -> None:
        code, _ = self.juno(
            "thread", "close", "--line", "3", "--was", "retry policy", "--evidence", self.QUOTE,
            env={"CLAUDE_CODE_AGENT_ID": "a"},
        )  # fmt: skip
        self.assertEqual(code, 2)
        self.assertEqual(self.get("threads.md"), THREADS)

    def test_forms_are_exclusive_and_complete(self) -> None:
        code, out = self.juno(
            "thread", "close", "t111111", "--line", "3", "--was", "retry policy",
            "--evidence", self.QUOTE,
        )  # fmt: skip
        self.assertEqual((code, "not both" in out), (2, True))
        self.assertEqual(self.juno("thread", "close", "--line", "3")[0], 2)
        self.assertEqual(self.juno("thread", "close")[0], 2)
        self.assertEqual(self.get("threads.md"), THREADS)

    def test_forget_points_at_the_direct_form(self) -> None:
        code, out = self.juno(
            "forget", "threads.md:3", "--was", "retry policy", "--evidence", self.QUOTE
        )
        self.assertEqual((code, "--line" in out), (2, True))


if __name__ == "__main__":
    unittest.main()
