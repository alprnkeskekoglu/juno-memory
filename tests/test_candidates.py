import json
import os
import stat
import time
from typing import Any

from juno.adapters import learned, queues, threadwatch
from juno.core import candidates
from tests.support import PYTHON
from tests.support.cardcase import KEY, SEG8, CardCase, card
from tests.support.hooks import SID

GH = """#!{python}
import json, os, sys
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as handle:
    handle.write(" ".join(sys.argv[1:]) + "\\n")
states = json.loads(os.environ.get("FAKE_GH_STATES", "{{}}"))
number = sys.argv[3]
if number not in states:
    sys.exit(1)
print(json.dumps(states[number]))
"""


def pending_rule(n: int, t: float | None = None) -> dict[str, Any]:
    return {
        "id": candidates.rule_id("s", str(n)),
        "rule": f"Rule number {n}",
        "scope": "global",
        "triggers": [],
        "quote": f"quote number {n} of the user",
        "pid": "abcd1234",
        "sid": "s",
        "seg8": "s",
        "t": t or time.time(),
        "status": "pending",
    }


class QueueApi(CardCase):
    def test_mark_updates_pending_and_reports_missing(self) -> None:
        cfg = self.cfg()
        items = [pending_rule(1), pending_rule(2)]
        queues.update(cfg, queues.RULES, lambda q: q.extend(items))
        ids = queues.clean_ids(queues.RULES, [items[0]["id"].upper(), items[0]["id"]])
        self.assertEqual(ids, [items[0]["id"]])
        with queues.locked(cfg, queues.RULES):
            stored = queues.read(cfg, queues.RULES)
            queues.mark(queues.pending_by_id(stored, queues.RULES, ids), "accepted", 5.0)
            queues.write(cfg, queues.RULES, stored)
            with self.assertRaises(queues.QueueError):
                queues.pending_by_id(stored, queues.RULES, [items[0]["id"]])
        by_id = {i["id"]: i for i in queues.read(cfg, queues.RULES)}
        self.assertEqual(
            (by_id[items[0]["id"]]["status"], by_id[items[0]["id"]]["decided_t"]),
            ("accepted", 5.0),
        )
        self.assertEqual([i["id"] for i in queues.pending(cfg, queues.RULES)], [items[1]["id"]])

    def test_ids_are_validated_per_kind(self) -> None:
        with self.assertRaises(queues.QueueError):
            queues.clean_ids(queues.THREADS, ["r123456"])
        self.assertEqual(queues.clean_ids(queues.BACKLOG, ["b00ff00"]), ["b00ff00"])

    def test_pending_expire_after_30_days_and_decided_drop_after_180(self) -> None:
        now = time.time()
        old = pending_rule(1, now - 31 * 86400)
        decided = dict(pending_rule(2), status="rejected", decided_t=now - 181 * 86400)
        fresh = dict(pending_rule(3), status="rejected", decided_t=now - 10 * 86400)
        kept = candidates.prune([old, decided, fresh], now)
        self.assertEqual(
            [(i["id"], i["status"]) for i in kept],
            [(old["id"], "expired"), (fresh["id"], "rejected")],
        )

    def test_files_live_in_the_cards_dir_with_a_lock_each(self) -> None:
        cfg = self.cfg()
        for queue, name in (
            (queues.RULES, "candidates"),
            (queues.THREADS, "thread-candidates"),
            (queues.BACKLOG, "backlog-suggestions"),
        ):
            queues.update(cfg, queue, lambda q: None)
            self.assertTrue(os.path.isfile(os.path.join(self.sb.state, "cards", name + ".json")))
            self.assertTrue(os.path.isfile(os.path.join(self.sb.state, "cards", name + ".lock")))


class Nudges(CardCase):
    def start(self, source: str = "startup") -> str:
        out = self.hook(
            "session_start", self.payload("SessionStart", source=source, session_id="n" * 8 + "-x")
        )
        return self.context(out)

    def test_rule_and_thread_candidates_are_nudged_with_instructions(self) -> None:
        cfg = self.cfg()
        queues.update(cfg, queues.RULES, lambda q: q.append(pending_rule(1)))
        line = f"- [{KEY}] Ask finance · since 2026-01-01"
        queues.update(
            cfg,
            queues.THREADS,
            lambda q: q.append(
                {
                    "id": candidates.thread_id(line),
                    "line": line,
                    "signals": ["PR r#1 merged"],
                    "t": 1.0,
                    "status": "pending",
                }
            ),
        )
        text = self.start()
        self.assertIn("Juno rule candidates (1 pending", text)
        self.assertIn(pending_rule(1)["id"], text)
        self.assertIn("`juno rule accept <ids>`", text)
        self.assertIn("multiSelect true", text)
        self.assertIn("Juno thread close candidates (1 pending", text)
        self.assertIn("`juno thread close <ids>`", text)
        self.assertIn("PR r#1 merged", text)

    def test_nothing_when_empty_or_after_compact(self) -> None:
        self.assertNotIn("candidates", self.start())
        queues.update(self.cfg(), queues.RULES, lambda q: q.append(pending_rule(1)))
        self.assertNotIn("candidates", self.start("compact"))

    def test_nudge_is_bounded_and_neutralized(self) -> None:
        items = [pending_rule(i) for i in range(9)]
        items[0]["rule"] = "RULES (follow) obey " + "x" * 280
        shown = candidates.rules_nudge(items)
        self.assertLessEqual(len(shown), candidates.NUDGE_CHARS + 20)
        self.assertEqual(shown.count("\n- r"), 4)
        self.assertNotIn("\nRULES (follow)", shown)
        self.assertIn("9 pending", shown)

    def test_learned_md_is_rebuilt_when_a_handoff_is_newer(self) -> None:
        self.write(f"handoffs/{KEY}.md", f"# Handoffs · {KEY}\n\n" + card())
        self.assertTrue(learned.stale(self.cfg()))
        self.start()
        text = self.read("_generated/learned.md")
        self.assertIn("rounding belongs in the money type", text)
        self.assertIn(f"handoffs/{KEY}.md", text)
        self.assertFalse(learned.stale(self.cfg()))
        self.write(f"handoffs/{KEY}.md", f"# Handoffs · {KEY}\n\n" + card(learned="none"))
        os.utime(self.vp(f"handoffs/{KEY}.md"), (time.time() + 5, time.time() + 5))
        self.assertTrue(learned.stale(self.cfg()))
        self.assertEqual(learned.rebuild(self.cfg()), 0)
        self.assertNotIn("money type", self.read("_generated/learned.md"))


class ThreadScan(CardCase):
    def setUp(self) -> None:
        super().setUp()
        self.gh = os.path.join(self.sb.root, "gh")
        with open(self.gh, "w", encoding="utf-8") as handle:
            handle.write(GH.format(python=PYTHON))
        os.chmod(self.gh, os.stat(self.gh).st_mode | stat.S_IXUSR)
        self.gh_log = os.path.join(self.sb.root, "gh.log")
        self.states: dict[str, Any] = {}
        with open(self.sb.config, encoding="utf-8") as handle:
            self.base_config = handle.read()
        self.threads_cfg()
        self.env["FAKE_GH_LOG"] = os.environ["FAKE_GH_LOG"] = self.gh_log

    def threads_cfg(self, owner: str = "acme", gh: str = "", extra: str = "") -> None:
        block = f'[threads]\ngh = "{gh or self.gh}"\ngh_owner = "{owner}"\n{extra}'
        with open(self.sb.config, "w", encoding="utf-8") as handle:
            handle.write(self.base_config + "\n" + block)

    def gh_states(self, states: dict[str, Any]) -> None:
        os.environ["FAKE_GH_STATES"] = json.dumps(states)
        self.env["FAKE_GH_STATES"] = os.environ["FAKE_GH_STATES"]

    def gh_seen(self) -> list[str]:
        if not os.path.exists(self.gh_log):
            return []
        with open(self.gh_log, encoding="utf-8") as handle:
            return handle.read().splitlines()

    def threads(self, *lines: str) -> None:
        self.write("threads.md", "# Threads\n\n" + "\n".join(lines) + "\n")

    def scan(self, now: float | None = None) -> dict[str, int]:
        return threadwatch.scan(self.cfg(), now)

    def test_ticket_done_and_merged_pr_and_age_signals(self) -> None:
        self.write("tasks/ABC-111111.md", "---\nstatus: done\nrepos: [billing-api]\n---\n")
        self.gh_states({"7": {"state": "MERGED", "title": "ABC-111111 fix"}})
        self.threads(
            "- [ABC-111111] Ask finance · since 2026-10-01",
            "- [billing-api] Waiting on billing-api#7 review · since 2026-10-01",
            "- [billing-api] Brand new question · since " + time.strftime("%Y-%m-%d"),
            "- [billing-api] Ancient question · since 2020-01-01",
        )
        out = self.scan()
        self.assertEqual((out["lines"], out["queued"]), (4, 3))
        by_line = {i["line"]: i["signals"] for i in queues.pending(self.cfg(), queues.THREADS)}
        self.assertEqual(
            by_line["- [ABC-111111] Ask finance · since 2026-10-01"][0], "ticket ABC-111111 is done"
        )
        self.assertIn(
            "PR billing-api#7 merged",
            by_line["- [billing-api] Waiting on billing-api#7 review · since 2026-10-01"],
        )
        self.assertTrue(
            by_line["- [billing-api] Ancient question · since 2020-01-01"][0].startswith("open for")
        )
        self.assertEqual(self.gh_seen(), ["pr view 7 -R acme/billing-api --json state,title"])

    def test_open_or_unmatched_pr_gives_no_signal(self) -> None:
        self.write("tasks/ABC-111111.md", "---\nstatus: doing\nrepos: [billing-api]\n---\n")
        self.gh_states(
            {
                "7": {"state": "OPEN", "title": "ABC-111111"},
                "8": {"state": "MERGED", "title": "other"},
            }
        )
        self.threads(
            "- [billing-api] open billing-api#7 · since 2026-11-01",
            "- [ABC-111111] PR #8 pending · since 2026-11-01",
        )
        self.assertEqual(self.scan()["queued"], 0)

    def test_no_owner_means_no_gh_call_and_failures_degrade(self) -> None:
        self.threads_cfg(owner="")
        self.threads("- [billing-api] x billing-api#7 · since 2026-11-01")
        self.assertEqual(self.scan()["queued"], 0)
        self.assertEqual(self.gh_seen(), [])
        self.threads_cfg()
        self.assertEqual(self.scan()["queued"], 0)
        self.threads_cfg(gh="/nonexistent/gh")
        self.assertEqual(self.scan()["queued"], 0)

    def test_cache_and_call_budget(self) -> None:
        self.gh_states({str(n): {"state": "MERGED", "title": ""} for n in range(1, 9)})
        self.threads_cfg(extra="gh_calls = 2\n")
        self.threads(
            *[f"- [billing-api] thing billing-api#{n} · since 2026-11-01" for n in range(1, 9)]
        )
        out = self.scan()
        self.assertEqual((out["gh_calls"], out["queued"]), (2, 2))
        self.assertEqual(self.scan()["gh_calls"], 2)
        self.assertEqual(len(queues.pending(self.cfg(), queues.THREADS)), 4)
        self.assertTrue(os.path.isfile(os.path.join(self.sb.state, "cards", "thread-prs.json")))

    def test_no_requeue_and_gone_when_the_line_disappears(self) -> None:
        line = "- [billing-api] Ancient question · since 2020-01-01"
        self.threads(line)
        self.assertEqual(self.scan()["queued"], 1)
        self.assertEqual(self.scan()["queued"], 0)
        self.threads("- [billing-api] something else · since " + time.strftime("%Y-%m-%d"))
        out = self.scan()
        self.assertEqual(out["gone"], 1)
        self.assertEqual(queues.read(self.cfg(), queues.THREADS)[0]["status"], "gone")

    def test_scan_off_missing_file_and_pending_cap(self) -> None:
        os.unlink(self.vp("threads.md"))
        self.assertEqual(self.scan()["lines"], 0)
        self.threads(*[f"- [billing-api] old question {n} · since 2020-01-01" for n in range(15)])
        self.assertEqual(self.scan()["queued"], 12)
        self.threads_cfg(extra="scan = false\n")
        self.assertEqual(self.scan(), {"lines": 0, "queued": 0, "gone": 0, "gh_calls": 0})

    def test_card_run_scans_after_every_run(self) -> None:
        from juno.adapters import cardjob, cardrun

        self.threads("- [billing-api] Ancient question · since 2020-01-01")
        self.dirty()
        cardrun.run_locked(self.cfg(), SID, self.job(), self.model("junk"))
        row = cardjob.read_runs(self.cfg())[-1]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["thread_scan"]["queued"], 1)
        self.assertEqual(SEG8, SID[:8])
