import os
import time
from typing import Any

from juno.adapters import queues
from juno.core import candidates
from tests.support.hooks import SID
from tests.test_extract import ExtractCase


class Threads(ExtractCase):
    OTHER = (
        "- [other] Plugin cache is still 1.0.3 while 1.0.4 is installed · since "
        + time.strftime("%Y-%m-%d")
    )
    LINE = "- [billing-api] Decide on rounding mode · since " + time.strftime("%Y-%m-%d")

    def seed(self) -> None:
        self.write("threads.md", f"# Threads\n\n{self.LINE}\n{self.OTHER}\n")
        self.start()

    def proposal(self, line: str, **extra: object) -> dict[str, object]:
        return {
            "line": line,
            "reason": "the user fixed it in src/money.py this segment",
            "evidence": "src/money.py",
            **extra,
        }

    def pending(self) -> list[dict[str, Any]]:
        return queues.pending(self.cfg(), queues.THREADS)

    def test_resolved_thread_becomes_a_pending_candidate_never_a_close(self) -> None:
        self.seed()
        before = self.read("threads.md")
        row = self.ext(threads={"close": [self.proposal(self.OTHER)]})
        self.assertEqual(self.read("threads.md"), before)
        self.assertEqual(row["threads"]["closed"], [])
        self.assertEqual(row["threads"]["proposed"], 1)
        self.assertEqual(self.sections(row)["threads"], "ok")
        (item,) = self.pending()
        self.assertEqual(item["id"], candidates.thread_id(self.OTHER))
        self.assertEqual(item["line"], self.OTHER)
        self.assertEqual(item["reason"], "the user fixed it in src/money.py this segment")
        self.assertEqual(item["evidence"], "src/money.py")
        self.assertEqual(item["signals"], [item["reason"]])
        self.assertEqual(
            (item["status"], item["source"], item["seg8"]), ("pending", "session-end", SID[:8])
        )

    def test_prompt_shows_every_open_thread_and_asks_for_reasons(self) -> None:
        self.seed()
        self.ext()
        prompt = self.calls[0]["prompt"]
        self.assertIn(self.OTHER, prompt)
        self.assertIn(self.LINE, prompt)
        self.assertIn('"close": [{"line"', prompt)
        self.assertIn("near-duplicate", prompt)

    def test_unclear_proposals_are_dropped(self) -> None:
        self.seed()
        bad = [
            self.OTHER,  # old string form: no reason, no evidence
            self.proposal("- [billing-api] not there · since 2026-01-01"),
            self.proposal(self.OTHER, evidence="a quote that is nowhere in the transcript"),
            self.proposal(self.OTHER, reason="x"),
            self.proposal(self.OTHER, reason="mail ops@example.com about it"),
        ]
        for item in bad:
            self.redirty()
            row = self.ext(threads={"close": [item]})
            self.assertEqual(self.sections(row)["threads"], "dropped:invalid", item)
        self.assertEqual(self.pending(), [])

    def test_same_candidate_is_not_queued_twice_and_kept_stays_kept(self) -> None:
        self.seed()
        self.ext(threads={"close": [self.proposal(self.OTHER)]})
        self.redirty()
        self.ext(threads={"close": [self.proposal(self.OTHER)]})
        self.assertEqual(len(self.pending()), 1)
        queues.update(self.cfg(), queues.THREADS, lambda q: queues.mark(q, "kept"))
        self.redirty()
        self.ext(threads={"close": [self.proposal(self.OTHER)]})
        self.assertEqual(self.pending(), [])
        self.assertEqual(len(queues.read(self.cfg(), queues.THREADS)), 1)

    def test_open_still_works_beside_proposals(self) -> None:
        self.seed()
        threads = {
            "close": [self.proposal(self.LINE)],
            "open": [{"tag": "billing-api", "text": "Ask finance about half-cent refunds"}],
        }
        row = self.ext(threads=threads)
        text = self.read("threads.md")
        self.assertIn("Decide on rounding mode", text)
        self.assertRegex(
            text, r"- \[billing-api\] Ask finance about half-cent refunds · since \d{4}-"
        )
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(row["threads"]["proposed"], 1)

    def test_open_refusals(self) -> None:
        self.start()
        opens = [
            {"tag": "unknown", "text": "Ask finance about half-cent refunds"},
            {"tag": "billing-api", "text": "short"},
            {"tag": "billing-api", "text": "Contains · a middle dot in the text"},
            {"tag": "billing-api", "text": "Mail ops@example.com about the refunds"},
        ]
        for item in opens:
            self.redirty()
            row = self.ext(threads={"open": [item]})
            self.assertEqual(self.sections(row)["threads"], "dropped:invalid")
        self.assertNotIn("Ask finance", self.read("threads.md"))

    def test_symlinked_threads_file_is_refused(self) -> None:
        target = os.path.join(self.sb.root, "real-threads.md")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("# Threads\n")
        os.unlink(self.vp("threads.md"))
        os.symlink(target, self.vp("threads.md"))
        self.start()
        row = self.ext(
            threads={
                "open": [{"tag": "billing-api", "text": "Ask finance about half-cent refunds"}]
            }
        )
        self.assertEqual(self.sections(row)["threads"], "dropped:path")
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "# Threads\n")
