import json
import os
from typing import Any

from juno.adapters import learnio, ledgerio, writelog
from juno.adapters.learnkinds import ticket
from juno.adapters.learnkinds.base import Ctx
from juno.core import digest, ticketdigest
from tests.support.cardcase import KEY, CardCase, card, with_extract
from tests.support.hooks import OTHER_SID, SID

TASK = f"tasks/{KEY}.md"
SUMMARY = (
    "The refund total drifts by one cent when a partial refund is rounded per line item. "
    "Cause: rounding happens before the lines are summed. Approach: round once on the sum "
    "inside the money type and keep line items exact, with a regression test per currency."
)
AC1 = "A partial refund of three equal line items sums to the original charge to the cent."
AC2 = "Currencies with zero decimals (such as yen) are not rounded to two places."
WORK = f"branch feature/{KEY}, PR https://github.com/acme/billing-api/pull/41, review passed"
OUTCOME = "round 1: 4 cases passed, 1 blocked; blocked because the sandbox gateway was offline"


def say(text: str) -> str:
    return json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    )


def tool(name: str, args: dict[str, Any], tid: str = "") -> str:
    item = {"type": "tool_use", "name": name, "input": args, **({"id": tid} if tid else {})}
    return json.dumps({"type": "assistant", "message": {"content": [item]}})


def result(tid: str, text: str) -> str:
    item = {"type": "tool_result", "tool_use_id": tid, "content": text}
    return json.dumps({"type": "user", "message": {"content": [item]}})


def line(section: str, key: str, text: str, evidence: str) -> dict[str, str]:
    return {"section": section, "key": key, "text": text, "evidence": evidence}


def extract(*lines: dict[str, str], key: str = KEY) -> dict[str, Any]:
    return {"ticket": [{"key": key, "lines": list(lines)}]}


class TicketCase(CardCase):
    def session(self, sid: str = SID) -> None:
        self.prompt(f"let us plan and fix {KEY}, the refund rounding bug", sid)
        self.edit(sid)

    def transcript(self, *rows: str) -> str:
        path = os.path.join(self.sb.root, "t.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(rows) + "\n")
        return path

    def hand_rows(self) -> list[str]:
        return [
            say(f"Plan for {KEY}. {SUMMARY}"),
            say(f"Acceptance criteria for {KEY}: 1. {AC1} 2. {AC2}"),
            tool("Bash", {"command": f"gh pr create --title {KEY}"}, "b1"),
            result("b1", "https://github.com/acme/billing-api/pull/41"),
            say(f"Opened the PR for {KEY}: https://github.com/acme/billing-api/pull/41"),
            say(f"Test report for {KEY}. {OUTCOME}"),
        ]

    def full(self) -> dict[str, Any]:
        return extract(
            line("Summary", "summary", SUMMARY, "round once on the sum inside the money type"),
            line("Criteria", "ac1", AC1, "sums to the original charge to the cent"),
            line("Criteria", "ac2", AC2, "Currencies with zero decimals"),
            line("Work", "billing-api", WORK, "https://github.com/acme/billing-api/pull/41"),
            line("Tests", "outcome", OUTCOME, "4 cases passed, 1 blocked"),
        )

    def go(self, data: dict[str, Any], rows: list[str] | None = None, **job: Any) -> dict[str, Any]:
        path = self.transcript(*(rows if rows is not None else self.hand_rows()))
        return self.run_card(with_extract(card(), data), transcript=path, **job)

    def task(self) -> str:
        return self.read(TASK) if os.path.exists(self.vp(TASK)) else ""

    def body(self, section: str) -> list[str]:
        text = self.task().split(f"## {section}\n")[1].split("\n## ")[0]
        return [x for x in text.splitlines() if x.strip()]


class HandSession(TicketCase):
    def test_a_hand_session_fills_the_sections_with_content(self) -> None:
        self.session()
        row = self.go(self.full())
        self.assertEqual(self.sections(row)["ticket"], "ok", row)
        text = self.task()
        self.assertIn("digests: [Summary, Criteria, Work, Tests]", text)
        self.assertIn(f"- [summary] {SUMMARY} · ", text)
        self.assertIn(f"- [ac1] {AC1} · ", text)
        self.assertIn(f"- [ac2] {AC2} · ", text)
        self.assertIn(f"- [billing-api] {WORK} · ", text)
        self.assertIn(f"- [outcome] {OUTCOME} · ", text)
        self.assertNotIn("## Cause", text)
        self.assertNotIn("## Decisions\n-", text)

    def test_every_written_line_is_anchored_in_the_digest(self) -> None:
        self.session()
        self.go(self.full())
        prompt = str(self.calls[0]["prompt"])
        for item in self.full()["ticket"][0]["lines"]:
            self.assertIn(item["evidence"], prompt)

    def test_one_write_per_ticket_and_undo(self) -> None:
        self.session()
        self.go(self.full())
        rows = [r for r in writelog.read(self.cfg()) if r["file"] == TASK]
        self.assertEqual([r["verb"] for r in rows], ["task-log"])
        self.assertTrue(rows[0]["engine"])
        self.assertEqual(rows[0]["created"], True)

    def test_status_and_decisions_are_not_touched(self) -> None:
        self.write(
            TASK,
            f"---\njira: {KEY}\ntitle: Refund rounding\nstatus: in-review\n---\n\n"
            "## Decisions\n- keep cents (user pid00001, 2026-01-01)\n\n## Notes\n- a note\n",
        )
        self.session()
        self.go(self.full())
        text = self.task()
        self.assertIn("status: in-review", text)
        self.assertIn("- keep cents (user pid00001, 2026-01-01)", text)
        self.assertIn("- a note", text)

    def test_a_missing_task_file_is_created(self) -> None:
        self.assertFalse(os.path.exists(self.vp(TASK)))
        self.session()
        self.go(self.full())
        text = self.task()
        self.assertTrue(text.startswith("---\n"))
        self.assertIn(f"jira: {KEY}", text)
        self.assertIn("created: ", text)
        self.assertNotIn("status:", text)

    def test_cause_section_only_when_given(self) -> None:
        self.session()
        cause = "Rounding per line item before summing, introduced by https://github.com/acme/billing-api/pull/12"
        data = extract(line("Cause", "cause", cause, "https://github.com/acme/billing-api/pull/41"))
        self.assertEqual(self.sections(self.go(data))["ticket"], "ok")
        self.assertEqual(self.body("Cause"), [f"- [cause] {cause} · {self.today()}"])

    def today(self) -> str:
        import time

        return time.strftime("%Y-%m-%d")


class LongSession(TicketCase):
    def test_summary_and_criteria_shown_early_are_written_in_full(self) -> None:
        self.session()
        rows = self.hand_rows()[:3]
        rows += [say(f"filler step {n} " + "x" * 700) for n in range(60)]
        rows += [
            result("b1", "https://github.com/acme/billing-api/pull/41"),
            say(f"Test report for {KEY}. {OUTCOME}"),
        ]
        data = extract(
            line("Summary", "summary", SUMMARY, "round once on the sum inside the money type"),
            line("Criteria", "ac1", AC1, "sums to the original charge to the cent"),
        )
        self.assertEqual(self.sections(self.go(data, rows))["ticket"], "ok")
        prompt = str(self.calls[0]["prompt"])
        self.assertIn(f"Ticket {KEY}", prompt)
        self.assertIn(SUMMARY, prompt)
        self.assertNotIn("filler step 3 ", prompt.split(f"Ticket {KEY}")[1])
        self.assertIn(f"- [summary] {SUMMARY} · ", self.task())
        self.assertIn(f"- [ac1] {AC1} · ", self.task())

    def test_the_ticket_block_has_its_own_budget(self) -> None:
        self.session()
        rows = [say(f"{KEY} note {n} " + "y" * 900) for n in range(40)]
        path = self.transcript(*rows)
        cfg = self.cfg()
        facts = digest.facts_from_lines(open(path, encoding="utf-8").read().splitlines(), "")
        seg = self.seg()
        small = digest.build_digest(seg, facts, "", 3000, [], [], 0)
        both = digest.build_digest(seg, facts, "", 3000, [], [], 2500)
        self.assertEqual(both[: len(small)], small)
        extra = both[len(small) :]
        self.assertIn(f"Ticket {KEY}", extra)
        self.assertLessEqual(len(extra), 2500 + 200)
        self.assertEqual(cfg.get_int("handoff", "ticket_chars"), 6000)

    def test_chars_zero_turns_the_kind_off(self) -> None:
        self.set_config("[handoff]\nticket_chars = 0\n")
        self.session()
        self.go(self.full())
        self.assertNotIn('"ticket"', str(self.calls[0]["prompt"]))
        self.assertEqual(self.task(), "")


class SecondSegment(TicketCase):
    def test_changed_lines_are_replaced_and_the_rest_kept(self) -> None:
        self.session()
        self.go(self.full())
        self.session(OTHER_SID)
        again = "Round once on the summed total only; line items stay exact, with one test per currency."
        data = extract(
            line("Summary", "summary", again, "round once on the sum inside the money type"),
            line("Criteria", "ac1", AC1, "sums to the original charge to the cent"),
            line("Criteria", "ac3", "Refunds never exceed the original charge.", "4 cases passed"),
        )
        row = self.go(data, sid=OTHER_SID)
        self.assertEqual(self.sections(row)["ticket"], "ok", row)
        text = self.task()
        for key in ("summary", "ac1", "ac2", "ac3", "billing-api", "outcome"):
            self.assertEqual(text.count(f"- [{key}] "), 1, key)
        self.assertIn(f"- [summary] {again} · ", text)
        self.assertNotIn("drifts by one cent", text)
        self.assertIn(f"- [ac2] {AC2} · ", text)
        self.assertEqual(
            [x.split("]")[0] for x in self.body("Criteria")], ["- [ac1", "- [ac2", "- [ac3"]
        )


class MentionOnly(TicketCase):
    def mention(self, sid: str = SID) -> None:
        for n in range(11):
            self.prompt(f"what does {KEY} say about step {n}?", sid)

    def test_a_key_only_mentioned_writes_nothing_and_asks_for_nothing(self) -> None:
        self.mention()
        row = self.go(self.full(), [say(f"{KEY} is about the refund rounding in the money type")])
        self.assertNotEqual(self.sections(row)["ticket"], "ok")
        self.assertEqual(self.task(), "")
        self.assertNotIn('- "ticket"', str(self.calls[0]["prompt"]))

    def test_a_key_the_ledger_never_saw_is_dropped(self) -> None:
        self.session()
        row = self.go(extract(*self.full()["ticket"][0]["lines"], key="DEF-9"))
        self.assertEqual(self.sections(row)["ticket"], "dropped:unworked")
        self.assertFalse(os.path.exists(self.vp("tasks/DEF-9.md")))

    def test_at_most_three_tickets_and_twenty_lines(self) -> None:
        self.session()
        lines = [
            line("Criteria", f"ac{n}", f"{AC1} variant number {n}", "sums to the original charge")
            for n in range(1, 31)
        ]
        self.assertEqual(self.sections(self.go(extract(*lines)))["ticket"], "ok")
        self.assertEqual(len(self.body("Criteria")), 20)


class Dropped(TicketCase):
    def test_unanchored_and_private_lines_drop_alone(self) -> None:
        self.session()
        data = extract(
            line("Summary", "summary", SUMMARY, "round once on the sum inside the money type"),
            line("Criteria", "ac1", AC1, "text nobody wrote anywhere in this segment at all"),
            line("Criteria", "ac2", f"{AC2} Token ghp_{'a' * 36}", "Currencies with zero decimals"),
            line("Tests", "outcome", OUTCOME, "4 cases passed, 1 blocked"),
        )
        row = self.go(data)
        self.assertEqual(self.sections(row)["ticket"], "ok")
        text = self.task()
        self.assertIn("[summary]", text)
        self.assertIn("[outcome]", text)
        self.assertNotIn("[ac1]", text)
        self.assertNotIn("[ac2]", text)
        self.assertEqual(row["ticket"]["dropped"], {"unanchored": 1, "private": 1})

    def test_all_lines_dropped_records_the_reason(self) -> None:
        self.session()
        data = extract(line("Summary", "summary", SUMMARY, "text nobody wrote anywhere at all"))
        row = self.go(data)
        self.assertEqual(self.sections(row)["ticket"], "dropped:unanchored")
        self.assertEqual(self.task(), "")

    def test_no_outside_path_and_no_bare_count(self) -> None:
        self.session()
        bad = [
            ("Work", "billing-api", "Branch is checked out in /srv/work/billing-api for review"),
            ("Work", "other", "Notes live in ~/notes/refund-rounding.md for later"),
            ("Criteria", "ac1", "5 ACs"),
            ("Criteria", "ac2", "3 of 5 criteria"),
            ("Tests", "outcome", "12 cases passed"),
        ]
        data = extract(*(line(s, k, t, f"{KEY}") for s, k, t in bad))
        data["ticket"][0]["lines"].append(
            line("Work", "billing-api", WORK, "https://github.com/acme/billing-api/pull/41")
        )
        row = self.go(data)
        self.assertEqual(self.sections(row)["ticket"], "ok")
        self.assertEqual(self.body("Work")[0][:16], "- [billing-api] ")
        self.assertEqual(len(self.body("Work")), 1)
        self.assertNotIn("Criteria", self.task())
        self.assertEqual(row["ticket"]["dropped"], {"path": 2, "bare": 3})

    def test_bad_section_key_and_length_are_dropped(self) -> None:
        self.session()
        data = extract(
            line("Decisions", "x", "user decision text that is long enough", KEY),
            line("Summary", "Bad Key", "a summary line long enough here", KEY),
            line("Summary", "summary", "x " * 500 + "xx", KEY),
            line("Summary", "summary", "a b c", KEY),
            line("Criteria", "ac1", "x " * 500, KEY),
        )
        row = self.go(data)
        self.assertEqual(self.sections(row)["ticket"], "ok")
        self.assertEqual(self.body("Criteria"), [f"- [ac1] {'x ' * 499}x · {self.today()}"])
        self.assertEqual(row["ticket"]["dropped"], {"section": 1, "key": 1, "invalid": 2})

    def today(self) -> str:
        import time

        return time.strftime("%Y-%m-%d")


class Skipped(TicketCase):
    def test_nosave_writes_nothing(self) -> None:
        self.session()
        self.prompt("later [nosave]")
        row = self.go(self.full())
        self.assertEqual(row["status"], "skipped")
        self.assertEqual(self.task(), "")

    def test_a_private_segment_writes_nothing(self) -> None:
        self.session()
        ledgerio.append(self.cfg(), SID, {"ev": "private"})
        row = self.go(self.full())
        self.assertEqual(row["status"], "skipped")
        self.assertEqual(self.task(), "")

    def test_a_headless_session_records_nothing_and_writes_nothing(self) -> None:
        payload = self.payload("UserPromptSubmit", prompt=f"fix {KEY}", session_id=OTHER_SID)
        self.hook("user_prompt_submit", payload, env={"JUNO_TEST_INTERACTIVE": "0"})
        self.assertFalse(self.ledger(OTHER_SID))
        self.assertEqual(self.task(), "")


class Block(TicketCase):
    def events(self, *rows: tuple[str, str]) -> list[tuple[str, str]]:
        return list(rows)

    def test_messages_naming_the_key_or_following_a_call_on_it_newest_first(self) -> None:
        rows = self.events(
            ("assistant", f"first note on {KEY}"),
            ("assistant", "unrelated chatter"),
            ("tool", f"gh pr view {KEY}"),
            ("assistant", "the review passed with no findings"),
            ("user", "go on"),
            ("assistant", "still unrelated"),
            ("assistant", f"last note on {KEY.lower()}"),
        )
        block = ticketdigest.block([KEY], rows, 6000)
        self.assertLess(block.index("last note"), block.index("review passed"))
        self.assertLess(block.index("review passed"), block.index("first note"))
        self.assertNotIn("unrelated", block)

    def test_budget_keeps_the_newest_and_never_exceeds_it(self) -> None:
        rows = self.events(*[("assistant", f"{KEY} message {n} " + "z" * 300) for n in range(20)])
        block = ticketdigest.block([KEY], rows, 1000)
        self.assertLessEqual(len(block), 1000 + 200)
        self.assertIn("message 19", block)
        self.assertNotIn("message 0 ", block)

    def test_nothing_for_no_keys_or_no_budget(self) -> None:
        rows = self.events(("assistant", f"{KEY} note"))
        self.assertEqual(ticketdigest.block([], rows, 6000), "")
        self.assertEqual(ticketdigest.block([KEY], rows, 0), "")

    def test_the_instructions_name_no_tool(self) -> None:
        self.session()
        ctx = Ctx(self.cfg(), self.seg(), "digest text")
        text = "\n".join(ticket.instructions(ctx)).lower()
        self.assertIn('"ticket"', text)
        for word in ("jira", "slack", "github", "claude", "plugin", "pipeline", "confluence"):
            self.assertNotIn(word, text)
        self.assertIn("never a path or pointer to a file that is not in the vault", text)
        self.assertIn('"ticket"', "\n".join(learnio.instructions(self.cfg(), self.seg(), "d")))
