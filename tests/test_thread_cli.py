import json
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


if __name__ == "__main__":
    unittest.main()
