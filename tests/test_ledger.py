import json
import os
import subprocess
import time
import unittest

from juno import config
from juno.adapters import ledgerio
from juno.core import ledger, routing
from juno.core.detect import Tickets
from juno.core.ledger import Segment
from tests.support import PYTHON, REPO, SandboxCase
from tests.support.hooks import SID

RULES = ledger.Rules(tickets=Tickets(("ABC", "DEF")))


def seg(**kwargs: object) -> Segment:
    return Segment(SID, RULES)


def write_ledger(state: str, sid: str, tools: int, age: float = 0.0) -> str:
    directory = os.path.join(state, "ledgers")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, sid + ".jsonl")
    now = time.time() - age
    rows = [{"ev": "start", "t": now, "cwd": "/x", "repos": []}]
    rows += [{"ev": "tool", "t": now, "name": "Edit", "edits": [["r", None]]} for _ in range(tools)]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("".join(json.dumps(r) + "\n" for r in rows))
    os.utime(path, (now, now))
    return path


def prompt(pid: str, text: str, tickets: list[str], **extra: object) -> dict[str, object]:
    return {"ev": "prompt", "pid": pid, "text": text, "tickets": tickets, **extra}


class Routing(unittest.TestCase):
    def test_handoff_naming(self) -> None:
        s = seg()
        self.assertEqual(routing.handoff_targets(s)[0]["path"], "handoffs/misc.md")
        s.apply({"ev": "tool", "repos": [["a", None], ["b", None]]})
        self.assertEqual(
            [t["path"] for t in routing.handoff_targets(s)], ["handoffs/a.md", "handoffs/b.md"]
        )
        s.title = "Release prep"
        self.assertEqual(
            [t["path"] for t in routing.handoff_targets(s)], ["handoffs/release-prep.md"]
        )
        s.apply({"ev": "tool", "edits": [["c", "ABC-2"]], "repos": [["d", "ABC-1"]]})
        s.apply({"ev": "tool", "edits": [["c", "ABC-2"]]})
        target = routing.handoff_targets(s)[0]
        self.assertEqual((target["path"], target["key"]), ("handoffs/ABC-2.md", "ABC-2"))
        self.assertEqual(target["tickets"], ["ABC-1", "ABC-2"])

    def test_without_jira_everything_routes_by_name_repo_or_misc(self) -> None:
        s = Segment(SID, ledger.Rules())
        s.apply(prompt("p1", "ABC-222222", ["ABC-222222"]))
        s.apply({"ev": "tool", "edits": [["c", "ABC-2"]]})
        self.assertEqual(routing.handoff_targets(s)[0]["path"], "handoffs/c.md")
        s.title = "Named"
        self.assertEqual(routing.handoff_targets(s)[0]["path"], "handoffs/named.md")

    def test_typed_ticket_beats_agent_only_branch_hits(self) -> None:
        s = seg()
        s.apply(prompt("p1", "/maestro:plan ABC-222222", ["ABC-222222"]))
        for _ in range(175):
            s.apply({"ev": "tool", "agent": True, "repos": [["billing-api", "ABC-666666"]]})
        target = routing.handoff_targets(s)[0]
        self.assertEqual((target["path"], target["key"]), ("handoffs/ABC-222222.md", "ABC-222222"))
        self.assertEqual(target["tickets"], ["ABC-222222", "ABC-666666"])

    def test_main_session_edit_beats_typed_ticket(self) -> None:
        s = seg()
        s.apply(prompt("p1", "look at ABC-1", ["ABC-1"]))
        s.apply(
            {"ev": "tool", "agent": False, "edits": [["c", "ABC-2"]], "repos": [["c", "ABC-2"]]}
        )
        self.assertEqual(routing.handoff_targets(s)[0]["key"], "ABC-2")

    def test_start_and_main_hits_rank_below_typed_and_above_agent(self) -> None:
        s = seg()
        for _ in range(5):
            s.apply({"ev": "tool", "agent": True, "repos": [["a", "ABC-3"]]})
        self.assertEqual([t["path"] for t in routing.handoff_targets(s)], ["handoffs/a.md"])
        s.apply({"ev": "tool", "agent": False, "repos": [["b", "ABC-4"]]})
        self.assertEqual(routing.handoff_targets(s)[0]["key"], "ABC-4")
        s.apply({"ev": "start", "cwd": "/x", "repos": [["c", "ABC-5"]]})
        self.assertEqual(routing.handoff_targets(s)[0]["key"], "ABC-5")
        s.apply(prompt("p1", "ABC-6", ["ABC-6"]))
        self.assertEqual(routing.handoff_targets(s)[0]["key"], "ABC-5")
        s.apply(prompt("p2", "ABC-4", ["ABC-4"]))
        self.assertEqual(routing.handoff_targets(s)[0]["key"], "ABC-4")
        s.apply(prompt("p3", "/maestro:plan ABC-6", ["ABC-6"], cmd_tickets=["ABC-6"]))
        self.assertEqual(routing.handoff_targets(s)[0]["key"], "ABC-6")

    def mentioned_only(self, title: str | None = None) -> Segment:
        s = seg()
        s.apply({"ev": "start", "cwd": "/x/claude", "repos": []})
        s.title = title
        for pid, ticket in (
            ("p1", "ABC-222222"),
            ("p2", "ABC-222222"),
            ("p3", "ABC-666666"),
            ("p4", "ABC-555555"),
        ):
            s.apply(prompt(pid, f"what about {ticket}?", [ticket], cmd_tickets=[]))
        s.apply({"ev": "tool", "agent": True, "repos": [["billing-api", "ABC-666666"]]})
        return s

    def test_mentioned_tickets_never_pick_the_target(self) -> None:
        s = self.mentioned_only()
        self.assertEqual((s.target_ticket(), s.current_ticket()), (None, None))
        target = routing.handoff_targets(s)[0]
        self.assertEqual((target["path"], target["key"]), ("handoffs/billing-api.md", "-"))
        self.assertEqual(target["tickets"], ["ABC-222222", "ABC-666666", "ABC-555555"])

    def test_named_session_with_mentions_routes_to_name(self) -> None:
        s = self.mentioned_only(title="juno")
        target = routing.handoff_targets(s)[0]
        self.assertEqual((target["path"], target["key"]), ("handoffs/juno.md", "-"))

    def test_each_corroboration_qualifies_a_typed_ticket(self) -> None:
        signals = {
            "edit": {"ev": "tool", "agent": False, "edits": [["r", "ABC-9"]]},
            "main": {"ev": "tool", "agent": False, "repos": [["r", "ABC-9"]]},
            "start": {"ev": "start", "cwd": "/x", "repos": [["r", "ABC-9"]]},
            "command": prompt("c", "/maestro:plan ABC-9", ["ABC-9"], cmd_tickets=["ABC-9"]),
        }
        for name, rec in signals.items():
            s = seg()
            for pid in ("p1", "p2", "p3"):
                s.apply(prompt(pid, "ABC-8", ["ABC-8"]))
            s.apply(prompt("p4", "and ABC-9", ["ABC-9"]))
            self.assertIsNone(s.target_ticket(), name)
            s.apply(rec)
            self.assertEqual(s.current_ticket(), "ABC-9", name)

    def test_command_tickets_need_a_leading_slash_command(self) -> None:
        cases = {
            "/maestro:plan ABC-222222": ["ABC-222222"],
            "  /maestro:plan  abc-2 and ABC-3\nABC-4": ["ABC-2", "ABC-3"],
            "/review": [],
            "look at /maestro:plan ABC-1": [],
            "/Users/a/ABC-1 x": [],
            "ABC-1 /x ABC-2": [],
        }
        for text, keys in cases.items():
            self.assertEqual(RULES.tickets.command_tickets(text), keys, text)

    def test_command_counter_reads_field_or_legacy_text(self) -> None:
        s = seg()
        s.apply(prompt("p1", "/x ABC-1", ["ABC-1"], cmd_tickets=[]))
        s.apply(prompt("p2", "x", ["ABC-2"], cmd_tickets=["ABC-2", "ABC-3"]))
        s.apply(prompt("p3", "/maestro:plan ABC-4", ["ABC-4"]))
        self.assertEqual(dict(s.command_tickets), {"ABC-2": 1, "ABC-4": 1})

    def test_synthetic_command_prompt_never_counts(self) -> None:
        s = seg()
        text = "<command-name>/maestro:plan</command-name>\n<command-args>ABC-1</command-args>"
        s.apply(prompt("p1", text, ["ABC-1"], cmd_tickets=["ABC-1"]))
        self.assertEqual((dict(s.command_tickets), s.current_ticket(), s.turns), ({}, None, 0))

    def test_session_name_overrides_ticket_guessing_until_an_edit(self) -> None:
        s = seg()
        s.title = "Juno routing"
        s.apply({"ev": "start", "cwd": "/x", "repos": [["a", "ABC-5"]]})
        s.apply({"ev": "tool", "agent": False, "repos": [["b", "ABC-4"]]})
        s.apply(prompt("p1", "/maestro:plan ABC-6", ["ABC-6"], cmd_tickets=["ABC-6"]))
        self.assertEqual(s.target_ticket(), "ABC-6")
        self.assertIsNone(s.current_ticket())
        self.assertEqual(
            [t["path"] for t in routing.handoff_targets(s)], ["handoffs/juno-routing.md"]
        )
        s.apply({"ev": "tool", "agent": False, "edits": [["b", "ABC-4"]]})
        self.assertEqual(routing.handoff_targets(s)[0]["path"], "handoffs/ABC-4.md")

    def test_session_named_after_a_ticket_routes_to_it(self) -> None:
        s = seg()
        s.apply({"ev": "title", "title": "ABC-222222"})
        s.apply(prompt("p1", "/maestro:plan ABC-222222", ["ABC-222222"]))
        for _ in range(20):
            s.apply({"ev": "tool", "agent": True, "repos": [["billing-api", "ABC-666666"]]})
        self.assertEqual(s.current_ticket(), "ABC-222222")
        named = seg()
        named.title = "abc-7 cleanup"
        self.assertEqual(routing.handoff_targets(named)[0]["path"], "handoffs/ABC-7.md")

    def test_card_targets_follow_the_written_card(self) -> None:
        s = seg()
        s.apply({"ev": "start", "cwd": "/x", "repos": [["r", "ABC-1"]]})
        self.assertEqual([t["path"] for t in routing.card_targets(s)], ["handoffs/ABC-1.md"])
        s.apply({"ev": "tool", "card": "handoffs/juno.md"})
        self.assertEqual([t["path"] for t in routing.card_targets(s)], ["handoffs/juno.md"])
        s.apply({"ev": "card", "path": "handoffs/a.md,handoffs/b.md", "auto": True})
        self.assertEqual(
            [t["path"] for t in routing.card_targets(s)], ["handoffs/a.md", "handoffs/b.md"]
        )

    def test_agent_hits_do_not_move_current_repo_or_ticket(self) -> None:
        s = seg()
        s.apply({"ev": "tool", "agent": False, "repos": [["billing-api", "ABC-111111"]]})
        s.apply({"ev": "tool", "agent": True, "repos": [["infra-charts", "ABC-555555"]]})
        self.assertEqual((s.last_repo, s.current_ticket()), ("billing-api", "ABC-111111"))
        only = seg()
        only.apply({"ev": "tool", "agent": True, "repos": [["infra-charts", "ABC-555555"]]})
        self.assertEqual((only.last_repo, only.current_ticket()), (None, None))


class SegmentState(unittest.TestCase):
    def test_meaningful_thresholds_and_dirty(self) -> None:
        s = seg()
        for _ in range(14):
            s.apply({"ev": "tool", "name": "Read"})
        self.assertFalse(s.meaningful or s.dirty)
        s.apply({"ev": "tool", "name": "Read"})
        self.assertTrue(s.meaningful and s.dirty)
        s.apply({"ev": "card", "path": "handoffs/x.md"})
        self.assertFalse(s.dirty)
        for _ in range(40):
            s.apply({"ev": "tool", "name": "Read"})
        self.assertTrue(s.dirty)

    def test_edit_makes_it_meaningful_and_boundary_dirty(self) -> None:
        s = seg()
        s.apply({"ev": "tool", "edits": [["r", None]]})
        self.assertTrue(s.meaningful and s.dirty)
        s.apply({"ev": "card", "path": "handoffs/x.md"})
        s.apply({"ev": "tool", "boundary": True})
        self.assertTrue(s.dirty)

    def test_nosave_private_fail_and_end_events(self) -> None:
        s = seg()
        s.apply(prompt("p1", "hello [nosave]", [], nosave=True))
        s.apply({"ev": "private", "why": "visibility"})
        s.apply({"ev": "fail", "tool": "Bash", "class": "exit"})
        s.apply({"ev": "end", "reason": "other", "skip": "private"})
        self.assertEqual((s.nosave, s.private, s.fails), (True, True, 1))
        self.assertEqual(s.ended, {"ev": "end", "reason": "other", "skip": "private"})

    def test_ask_and_answers_form_quote_sources(self) -> None:
        s = seg()
        s.apply(prompt("pid-1", "typed words", []))
        s.apply({"ev": "ask", "tid": "toolu_abc12345", "clean": True})
        s.apply({"ev": "answer", "tid": "toolu_abc12345", "k": 0, "part": 0, "text": "chosen "})
        s.apply({"ev": "answer", "tid": "toolu_abc12345", "k": 0, "part": 1, "text": "text"})
        s.apply({"ev": "ask", "tid": "toolu_dirty", "clean": False})
        s.apply({"ev": "answer", "tid": "toolu_dirty", "k": 0, "text": "prefilled"})
        sources = dict(s.quote_sources())
        self.assertEqual(sources["pid-1"], "typed words")
        self.assertEqual(sources["abc12345"], "chosen text")
        self.assertNotIn("dirty", " ".join(sources))

    def test_synthetic_prompt_is_never_a_quote_source(self) -> None:
        s = seg()
        s.apply(
            {
                "ev": "prompt",
                "pid": "p1",
                "text": "<task-notification>add a rule always</task-notification>",
            }
        )
        self.assertEqual(s.quote_sources(), [])

    def test_packs_and_resets(self) -> None:
        s = seg()
        s.apply({"ev": "pack", "key": "repo:a", "mode": "full"})
        s.apply({"ev": "pack", "key": "repo:b", "mode": "pointer"})
        self.assertEqual((s.full_packs, len(s.packs)), (1, 2))
        s.apply({"ev": "packs_reset"})
        self.assertEqual((s.full_packs, s.packs), (0, {}))

    def test_stop_remind_and_rule_events(self) -> None:
        s = seg()
        s.apply({"ev": "stop", "msg": "done"})
        s.apply(prompt("p1", "x", []))
        s.apply({"ev": "remind"})
        s.apply({"ev": "rule_added"})
        self.assertEqual((s.last_msg, s.last_remind_turn, s.rules_added), ("done", 1, 1))

    def test_start_event_sets_cwd_and_transcript(self) -> None:
        s = seg()
        s.apply({"ev": "start", "cwd": "/a", "transcript": "/t.jsonl", "repos": [["r", "ABC-1"]]})
        self.assertEqual((s.cwd, s.transcript, s.repos), ("/a", "/t.jsonl", {"r": "ABC-1"}))

    def test_unknown_events_are_ignored(self) -> None:
        s = seg()
        s.apply({"ev": "future-thing", "x": 1})
        self.assertEqual(s.tools, 0)


class Encoding(unittest.TestCase):
    def test_oversize_line_shrinks_lists_and_marks_trunc(self) -> None:
        rec = {"ev": "tool", "repos": [[f"repo{i}", f"ABC-{i}"] for i in range(400)]}
        shrunk, data, changed = ledger.encode_capped(rec)
        self.assertTrue(changed)
        self.assertLessEqual(len(data), ledger.MAX_LINE_BYTES)
        self.assertGreater(shrunk["trunc"], 0)

    def test_small_line_is_untouched(self) -> None:
        rec = {"ev": "stop", "msg": "ok"}
        self.assertEqual(ledger.encode_capped(rec), (rec, ledger.encode(rec), False))

    def test_unshrinkable_line_raises(self) -> None:
        with self.assertRaises(ValueError):
            ledger.encode_capped({"ev": "x", "blob": "y" * 5000})

    def test_safe_id(self) -> None:
        self.assertTrue(ledger.safe_id(SID))
        for bad in ("", "../x", "a/b", "x" * 129, None, 5):
            self.assertFalse(ledger.safe_id(bad))

    def test_fold_lines_skips_corrupt_lines(self) -> None:
        lines = [b'{"ev":"tool","name":"a"}', b"not json", b'["list"]', b'{"ev":"tool","name":"b"}']
        self.assertEqual(ledger.fold_lines(SID, lines, RULES).tools, 2)


class LedgerIo(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.cfg = config.load(dict(self.sb.env))

    def test_append_fold_and_private_dirs(self) -> None:
        ledgerio.append(self.cfg, SID, {"ev": "tool", "name": "x"})
        ledgerio.append(self.cfg, SID, {"ev": "tool", "name": "y"})
        self.assertEqual(ledgerio.fold(self.cfg, SID).tools, 2)
        mode = os.stat(os.path.join(self.sb.state, "ledgers")).st_mode & 0o777
        self.assertEqual(mode, 0o700)
        self.assertEqual(os.stat(ledgerio.ledger_path(self.cfg, SID) or "").st_mode & 0o777, 0o600)

    def test_unsafe_session_id_is_ignored(self) -> None:
        self.assertIsNone(ledgerio.append(self.cfg, "../evil", {"ev": "tool"}))
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "evil.jsonl")))

    def test_oversize_record_is_counted(self) -> None:
        rec = {"ev": "tool", "repos": [[f"r{i}", None] for i in range(800)]}
        ledgerio.append(self.cfg, SID, rec)
        with open(os.path.join(self.sb.state, "counters.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["ledger_truncated"], 1)

    def test_concurrent_appenders_interleave_whole_lines(self) -> None:
        script = (
            "import sys\nsys.path.insert(0, sys.argv[1])\n"
            "from juno import config\nfrom juno.adapters import ledgerio\n"
            "cfg = config.load()\n"
            "for i in range(int(sys.argv[3])):\n"
            "    ledgerio.append(cfg, sys.argv[2], {'ev': 'tool', 'name': 'w%s-%d' % (sys.argv[4], i), 'pad': 'x' * 2000})\n"
        )
        procs = [
            subprocess.Popen(
                [PYTHON, "-c", script, REPO, SID, "40", str(n)],
                env=self.sb.env,
                stdout=subprocess.DEVNULL,
            )
            for n in range(3)
        ]
        for proc in procs:
            self.assertEqual(proc.wait(timeout=60), 0)
        self.assertEqual(ledgerio.fold(self.cfg, SID).tools, 120)

    def test_record_applies_to_the_segment(self) -> None:
        s = ledgerio.fold(self.cfg, SID)
        ledgerio.record(self.cfg, s, {"ev": "title", "title": "T"})
        self.assertEqual(s.title, "T")

    def test_synthetic_report_and_purge(self) -> None:
        path = ledgerio.ledger_path(self.cfg, SID) or ""
        syn = "<task-notification>x</task-notification>"
        rows = [
            {"ev": "prompt", "pid": "p1", "part": 0, "text": syn},
            {"ev": "prompt", "pid": "p1", "part": 1, "text": "more"},
            {"ev": "prompt", "pid": "p2", "part": 0, "text": "typed"},
            {"ev": "tool", "name": "x"},
        ]
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("".join(json.dumps(r) + "\n" for r in rows))
        before = ledgerio.fold(self.cfg, SID)
        self.assertEqual((before.turns, before.tools), (1, 1))
        self.assertEqual(
            ledgerio.synthetic_report(self.cfg), {"ledgers": 1, "prompts": 1, "records": 2}
        )
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(len(handle.read().splitlines()), 4)
        done = ledgerio.purge_synthetic(self.cfg)
        self.assertEqual((done["ledgers"], done["prompts"], done["records"]), (1, 1, 2))
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(len(handle.read().splitlines()), 2)
        self.assertEqual(ledgerio.synthetic_report(self.cfg)["records"], 0)
        backups = os.listdir(os.path.join(self.sb.state, "backups"))
        self.assertEqual(len(backups), 1)


if __name__ == "__main__":
    unittest.main()
