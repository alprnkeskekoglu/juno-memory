import json
import os
import time
import unittest

from juno.adapters import queues
from juno.core import doctor_vault as dv
from tests.support import timing
from tests.support.write import SID, WriteCase

CARD = (
    "## 2026-11-01 10:00 · - · work · abcd1234\nRepos: app\nTickets: -\nDid a thing.\n"
    "Learned: none\n"
)


def names(checks: list[dv.Check]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for level, name, _ in checks:
        found.setdefault(name, set()).add(level)
    return found


class Pure(unittest.TestCase):
    def test_cards(self) -> None:
        good = {"valid": True, "path": "handoffs/a.md", "seg": "abcd1234", "head": "", "auto": True}
        good["body"] = "## h\nRepos: a\nTickets: -\nLearned: x"
        bad_fields = dict(good, seg="ffff0000", auto=False, body="## h\nRepos: a")
        broken = {"valid": False, "path": "handoffs/b.md", "head": "## nope", "body": ""}
        out = dv.card_checks([good, good, bad_fields, broken])
        levels = names(out)
        self.assertEqual(levels["cards"], {"FAIL"})
        self.assertEqual(levels["stubs"], {"WARN"})
        self.assertEqual(levels["duplicate cards"], {"WARN"})
        self.assertTrue(any("lacks Tickets, Learned" in d for _, _, d in out))
        said = {n: d for _, n, d in out if n in ("stubs", "duplicate cards")}
        self.assertIn("juno card --segment abcd1234", said["stubs"])
        self.assertIn("juno card dismiss abcd1234", said["stubs"])
        self.assertIn("juno card merge a abcd1234", said["duplicate cards"])

    def test_rules_provenance_caps_and_kinds(self) -> None:
        files = {
            "global.md": "- 2026-01-01 · ok · src: unquoted\n- bad line\n",
            "repos/app.md": "- 2026-01-01 · a · src: unquoted\n- 2026-01-01 · b · src: unquoted\n",
            "ABC-12.md": "- 2026-01-01 · old · src: unquoted\n",
            "topics/xy.md": "# Rules\n\n- 2026-01-01 · t · src: unquoted\n",
        }
        out = dv.rule_checks(files, 80, 1, "none")
        details = " ".join(d for _, _, d in out)
        self.assertIn("rules/global.md:2 has no valid provenance", details)
        self.assertIn("rules/repos/app.md has 2 lines (cap 1)", details)
        self.assertIn("legacy ticket rules", details)
        self.assertIn("no triggers", details)
        self.assertTrue(any(level == "FAIL" for level, _, _ in out))

    def test_budget_fail_warn_pass(self) -> None:
        self.assertEqual(dv.budget_checks("x" * 10, "", 100, 100)[0][0], "PASS")
        self.assertEqual(dv.budget_checks("x" * 95, "", 100, 100)[0][0], "WARN")
        self.assertEqual(dv.budget_checks("x" * 101, "", 100, 100)[0][0], "FAIL")
        big = "- 2026-01-01 · " + "r" * 120 + " · src: unquoted\n"
        self.assertEqual(dv.budget_checks("", big, 100, 100)[0][0], "FAIL")

    def test_profiles_and_handoffs_and_learned(self) -> None:
        self.assertEqual(dv.profile_checks(["a", "b"], {"a"})[0][0], "WARN")
        self.assertEqual(dv.profile_checks(["a"], {"a"})[0][0], "PASS")
        self.assertEqual(dv.handoff_size_checks({"a.md": 21})[0][0], "WARN")
        self.assertEqual(dv.handoff_size_checks({"a.md": 20})[0][0], "PASS")
        self.assertEqual(dv.learned_check(True)[0][0], "WARN")
        self.assertEqual(dv.learned_check(None)[0][0], "INFO")

    def test_retire_checks(self) -> None:
        dead = [{"path": "knowledge/a.md", "ref": "[[x]]", "reason": "missing"}]
        out = dv.retire_checks([("knowledge/old.md", "knowledge/new.md")], dead)
        self.assertEqual([c[0] for c in out], ["PASS", "WARN"])

    def test_privacy(self) -> None:
        out = dv.privacy_checks({"notes/a.md": "mail me at someone@example.com", "b.md": "fine"})
        self.assertEqual(out[0][0], "WARN")
        self.assertIn("email", out[0][2])
        key = "ghp_" + "a" * 36
        self.assertIn("github", dv.privacy_checks({"a.md": key})[0][2])
        self.assertEqual(dv.privacy_checks({"a.md": "clean"})[0][0], "PASS")

    def test_notes_knowledge_pitfalls(self) -> None:
        self.assertEqual(dv.note_checks({"a.md": "x\n" * 61})[0][0], "WARN")
        out = dv.knowledge_checks({"a.md": ("x" * 8001, False)})
        self.assertEqual(len(out), 2)
        line = "- Use tabs · triggers: tabs · evidence · 2026-01-01"
        self.assertEqual(dv.pitfall_checks(line + "\n", "none")[0][0], "PASS")
        self.assertEqual(dv.pitfall_checks("- broken\n", "none")[0][0], "WARN")
        many = "\n".join(
            f"- w{i:03d}a w{i:03d}b w{i:03d}c w{i:03d}d w{i:03d}e · triggers: t{i} · e{i} · d"
            for i in range(61)
        )
        self.assertEqual(dv.pitfall_checks(many, "none")[0][0], "PASS")

    def test_near_duplicate_pitfall_pairs_are_flagged(self) -> None:
        err = "(eval):1: == not found"
        text = (
            "# Pitfalls\n\n"
            f"- zsh expands an unquoted == in echo ==== as equals expansion; quote it · triggers: zsh · {err}; segment a · 2026-11-01\n"
            "- A grep in a guessed path failed; locate the file repo-wide first · triggers: grep · e; segment a · 2026-11-01\n"
            f"- Separator lines of equals signs break there · triggers: echo · {err}; segment b · 2026-11-01\n"
        )
        out = dv.pitfall_checks(text, "none")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][:2], ("WARN", "pitfalls"))
        self.assertIn("1 near-duplicate pair (lines 3/5)", out[0][2])
        self.assertIn("juno forget pitfalls.md:<line>", out[0][2])

    def test_threads(self) -> None:
        now = time.mktime(time.strptime("2026-11-07", "%Y-%m-%d"))
        old = "- [app] an old open question · since 2026-01-01\n"
        new = "- [app] a new open question · since 2026-11-01\n"
        out = dv.thread_checks(old + new, 30, 2, now)
        details = " ".join(d for _, _, d in out)
        self.assertIn("threads.md:1 is older than 30 days", details)
        self.assertNotIn("threads.md:2", details)
        self.assertEqual(out[-1][0], "INFO")
        self.assertEqual(dv.thread_checks(new, 30, 0, now)[0][0], "PASS")

    def test_thread_summary_line(self) -> None:
        now = time.mktime(time.strptime("2026-11-11", "%Y-%m-%d"))
        text = (
            "- [app] an old open question · since 2026-11-01\n"
            "- [app] a newer open question · since 2026-11-09\n"
        )
        self.assertEqual(
            dv.thread_summary(text, now),
            ("INFO", "threads", "2 open, 1 older than 7 days (juno thread list --stale)"),
        )
        self.assertEqual(dv.thread_summary("", now)[2][:7], "0 open,")

    def test_backlog(self) -> None:
        row = {
            "slug": "a-b-c",
            "title": "Same",
            "status": "next",
            "priority": "p1",
            "due": "2026-01-01",
            "problem": None,
        }
        twin = dict(row, slug="a-b-d")
        bad = dict(row, slug="x-y-z", problem="bad status")
        out = dv.backlog_checks([row, twin, bad], "2026-11-07", 3, ("done", "dropped"))
        details = " ".join(d for _, _, d in out)
        self.assertIn("invalid frontmatter (bad status)", details)
        self.assertIn("2 open items share the title", details)
        self.assertIn("2 open (p1 2, p2 0, p3 0), 2 overdue, 3 pending", details)
        self.assertEqual(dv.backlog_checks([], "2026-11-07", 0, ())[0][2], "no backlog items")

    def test_links(self) -> None:
        files = {
            "core.md": "See [[links]] and [[handoffs/app|the app]] and [[ghost]] and `[[code]]`.\n```\n[[fenced]]\n```\n[[<repo>]]",
            "protocol/links.md": "back to [[core]]",
            "handoffs/app.md": "",
        }
        out = dv.link_checks(files)
        details = " ".join(d for _, _, d in out)
        self.assertIn("core.md:1 -> [[ghost]]", details)
        self.assertNotIn("fenced", details)
        self.assertNotIn("code", details.replace("ghost link targets", ""))
        self.assertEqual(dv.link_checks({"a.md": "[[a]]"})[0][0], "PASS")

    def test_timings_and_probe(self) -> None:
        lines = ["PreToolUse 10ms", "PreToolUse 20ms", "Stop 900ms", "junk"]
        out = dv.timing_checks(lines)
        self.assertEqual(out[0][0], "PASS")
        self.assertIn("p95", out[0][2])
        self.assertEqual(dv.timing_checks(["PreToolUse 80ms"] * 3)[0][0], "WARN")
        self.assertEqual(dv.timing_checks([])[0][0], "INFO")
        self.assertEqual(dv.probe_check([1.0, 2.0, 3.0])[0], "PASS")
        self.assertEqual(dv.probe_check([60.0, 70.0, 80.0])[0], "WARN")

    def test_errors_runs_learning_shadow_stale(self) -> None:
        self.assertEqual(dv.error_checks(0, None, 0)[0][0], "PASS")
        self.assertEqual(dv.error_checks(2, {"ts": "t", "kind": "K"}, 1)[0][0], "WARN")
        now = 1_800_000_000.0
        rows = [
            {"t": now - 10, "status": "ok", "cost_usd": 0.1, "rules": {"queued": 2}, "sid": "s1"},
            {"t": now - 5, "status": "failed", "error": "boom", "sid": "abcdefgh12"},
            {"t": now - 99 * 86400, "status": "ok"},
        ]
        out = dv.card_run_checks(rows, "end", now)
        self.assertEqual(out[0][0], "WARN")
        self.assertIn("1 ok, 1 failed, 0 skipped, $0.10", out[0][2])
        self.assertIn("abcdefgh failed", out[1][2])
        self.assertEqual(dv.card_run_checks([], "end", now)[0][0], "INFO")
        learning = dv.learning_checks(rows, {"rules": 1, "threads": 2, "backlog": 3}, now)
        self.assertIn("2 queued", learning[0][2])
        self.assertIn("backlog 3", learning[0][2])
        self.assertEqual(dv.shadow_checks(True, None, 0)[0][0], "INFO")
        self.assertIn("2 shadow entries", dv.shadow_checks(True, [now, now - 86400], 0)[0][2])
        self.assertEqual(dv.shadow_checks(True, [now], 2)[0][0], "WARN")
        self.assertEqual(dv.stale_cache_checks(None, None)[0][0], "INFO")
        self.assertEqual(dv.stale_cache_checks(["a"], "d")[0][0], "WARN")
        self.assertEqual(dv.stale_cache_checks([], "d")[0][0], "PASS")
        self.assertEqual(
            dv.synthetic_checks({"records": 2, "prompts": 1, "ledgers": 1})[0][0], "WARN"
        )


class Collected(WriteCase):
    def doctor(self, *extra: str) -> tuple[int, dict[str, list[tuple[str, str]]]]:
        code, out = self.juno("doctor", "--json", *self.sb.install_args(), *extra)
        grouped: dict[str, list[tuple[str, str]]] = {}
        for check in json.loads(out)["checks"]:
            grouped.setdefault(check["name"], []).append((check["status"], check["detail"]))
        return code, grouped

    def levels(self, name: str) -> set[str]:
        return {s for s, _ in self.doctor()[1].get(name, [])}

    def test_fresh_init_has_no_fail_and_only_environment_warns(self) -> None:
        _, grouped = self.doctor()
        warns = {n for n, rows in grouped.items() if any(s == "WARN" for s, _ in rows)}
        fails = {n for n, rows in grouped.items() if any(s == "FAIL" for s, _ in rows)}
        self.assertEqual(fails - {"shim", "plugin root", "settings"}, set())
        self.assertEqual(
            warns - {"plugin", "shim", "settings", "settings merge", "autoMemoryEnabled"}, set()
        )

    def test_json_skips_the_probe(self) -> None:
        self.assertEqual(self.doctor()[1]["pretooluse"][0][0], "INFO")

    def test_bad_card_and_stub_and_duplicate(self) -> None:
        self.put(
            "handoffs/app.md",
            "# Handoffs\n\n## not a heading\n" + CARD + CARD.replace("abcd1234", "abcd1234"),
        )
        grouped = self.doctor()[1]
        self.assertIn("FAIL", {s for s, _ in grouped["cards"]})
        self.assertIn("duplicate cards", grouped)

    def test_missing_profile_and_over_20_cards(self) -> None:
        cards = "".join(
            CARD.replace("2026-11-01", f"2026-10-{i:02d}").replace("abcd1234", f"{i:08d}")
            for i in range(1, 22)
        )
        self.put("handoffs/app.md", "# Handoffs\n\n" + cards)
        grouped = self.doctor()[1]
        self.assertEqual(grouped["profiles"][0][0], "WARN")
        self.assertEqual(grouped["handoffs"][0][0], "WARN")
        self.assertEqual(grouped["learned"][0][0], "INFO")

    def test_rule_without_provenance_fails(self) -> None:
        self.put("rules/global.md", "# Rules\n\n- hand written\n")
        code, grouped = self.doctor()
        self.assertEqual(code, 1)
        self.assertIn("FAIL", {s for s, _ in grouped["rules"]})

    def test_queue_counts_reach_the_learning_line(self) -> None:
        self.seed_queue(queues.RULES, [{"id": "r000001", "status": "pending", "t": 1.0}])
        self.assertIn("pending rules 1", self.doctor()[1]["learning"][0][1])

    def test_runs_jsonl_is_read(self) -> None:
        os.makedirs(os.path.join(self.sb.state, "cards"), exist_ok=True)
        row = {"t": time.time(), "status": "failed", "sid": SID, "error": "boom"}
        with open(os.path.join(self.sb.state, "cards", "runs.jsonl"), "w", encoding="utf-8") as h:
            h.write(json.dumps(row) + "\n")
        self.assertEqual(self.doctor()[1]["card runs"][0][0], "WARN")

    def test_hook_errors_and_timings(self) -> None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(os.path.join(self.sb.state, "hook-errors.log"), "w", encoding="utf-8") as h:
            h.write(f"{stamp} Stop ValueError x.py:1 'x'\n")
        with open(os.path.join(self.sb.state, "hook-timings.log"), "w", encoding="utf-8") as h:
            h.write("Stop 12ms\nStop 30ms\n")
        grouped = self.doctor()[1]
        self.assertEqual(grouped["hook errors"][0][0], "WARN")
        self.assertIn("Stop n=2", grouped["hook timings"][0][1])

    def test_index_readability(self) -> None:
        index = os.path.join(self.sb.state, "index.sqlite3")
        with open(index, "wb") as h:
            h.write(b"not a database" * 100)
        self.assertEqual(self.doctor()[1]["index"][0][0], "WARN")
        os.unlink(index)
        import sqlite3

        sqlite3.connect(index).close()
        self.assertEqual(self.doctor()[1]["index"][0][0], "PASS")

    def test_stale_cache_and_shadow_and_pitfalls(self) -> None:
        self.put("pitfalls.md", "# Pitfalls\n\n- broken line\n")
        with open(os.path.join(self.sb.state, "shadow.jsonl"), "w", encoding="utf-8") as h:
            h.write(json.dumps({"ts": time.time()}) + "\nnot json\n")
        grouped = self.doctor()[1]
        self.assertEqual(grouped["pitfalls"][0][0], "WARN")
        self.assertEqual(grouped["shadow"][0][0], "WARN")

    def test_privacy_threads_backlog_and_legacy_rules(self) -> None:
        self.put("notes/n.md", "contact: a.person@example.com\n")
        self.put("threads.md", "# Threads\n\n- [app] a very old open question · since 2020-01-01\n")
        self.put("backlog/bad-item.md", "---\ntitle: x\n---\n")
        self.put("rules/ABC-9.md", "# Rules\n\n- 2026-01-01 · r · src: unquoted\n")
        grouped = self.doctor()[1]
        self.assertEqual(grouped["privacy"][0][0], "WARN")
        self.assertIn("older than", grouped["threads"][0][1])
        self.assertEqual(grouped["backlog"][0][0], "WARN")
        self.assertTrue(any("legacy" in d for _, d in grouped["rules"]))

    def test_dead_supersedes(self) -> None:
        self.put("knowledge/a.md", "---\nsupersedes: [[missing-file]]\n---\nbody\n")
        self.assertIn("supersedes", {n for n in self.doctor()[1] if self.levels(n) == {"WARN"}})


class Purge(WriteCase):
    def test_synthetic_reported_and_purged(self) -> None:
        self.prompt("<task-notification>harness text</task-notification>", "pidsyn01")
        grouped = Collected.doctor(self)[1]  # type: ignore[arg-type]
        self.assertEqual(grouped["synthetic"][0][0], "WARN")
        code, out = self.juno("doctor", "--purge-synthetic")
        self.assertEqual(code, 0, out)
        self.assertIn("Juno: purged", out)
        self.assertEqual(Collected.doctor(self)[1]["synthetic"][0][0], "PASS")  # type: ignore[arg-type]

    def test_purge_refused_headless_and_in_agents(self) -> None:
        for extra in (
            {"CLAUDE_CODE_AGENT_ID": "a"},
            {"JUNO_TEST_INTERACTIVE": "", "CLAUDE_CODE_SESSION_ATTENDED": "0"},
        ):
            code, out = self.juno("doctor", "--purge-synthetic", env=extra)
            self.assertEqual(code, 2)
            self.assertTrue(out.startswith("Juno: refused: "))


@timing
class Probe(WriteCase):
    def test_probe_runs_in_process_without_json(self) -> None:
        _, out = self.juno("doctor", *self.sb.install_args())
        self.assertIn("pretooluse: fast path median", out)
        self.assertNotIn("FAIL", out.replace("FAIL plugin", ""))


if __name__ == "__main__":
    unittest.main()
