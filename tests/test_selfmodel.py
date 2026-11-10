import json
import os
import time
import unittest
from typing import Any

from juno.adapters import armseg, armsio, asklog, cardrun, learnio, ledgerio, selfio
from juno.core import armmetrics, arms, selfmodel
from tests.support import REPO
from tests.support.cardcase import CardCase
from tests.support.hooks import SID, HookCase
from tests.test_arms import put_arm

QUOTE = "I would rather ship the small fix today and refactor the module next week"
SOURCES = [("p1234567", QUOTE + ", please")]
TEXT = "Prefers the small fix now over the refactor when time is short"


def item(**over: object) -> dict[str, object]:
    return {"file": "values", "text": TEXT, "quote": QUOTE, **over}


def line(text: str, day: str) -> str:
    return f'- {text} · {day} · src: user abcd1234 · "{QUOTE}"'


class Check(unittest.TestCase):
    def check(self, found: object) -> tuple[str, dict[str, str]]:
        return selfmodel.check(found, SOURCES, "none", 20)

    def test_verbatim_quote_is_accepted_with_its_prompt_id(self) -> None:
        status, found = self.check(item())
        self.assertEqual((status, found["pid"]), ("ok", "p1234567"))

    def test_fabricated_quote_is_unverified(self) -> None:
        self.assertEqual(
            self.check(item(quote="never typed anywhere in this segment"))[0], "unverified"
        )

    def test_identity_claims_are_refused(self) -> None:
        for text in ("I am a hurried engineer who ships fast", "The user is Juno and ships fast"):
            self.assertEqual(self.check(item(text=text))[0], "identity", text)
        self.assertEqual(
            self.check(item(text="Is sure that you ship the small fix"))[0], "identity"
        )

    def test_shape_and_privacy(self) -> None:
        self.assertEqual(self.check(item(file="other"))[0], "invalid")
        self.assertEqual(self.check(item(text="x" * 201))[0], "invalid")
        self.assertEqual(self.check(item(text="Prefers a · separator in the text"))[0], "invalid")
        self.assertEqual(
            self.check(item(text="Mails me at someone@example.com first"))[0], "private"
        )
        self.assertEqual(self.check("text")[0], "invalid")


class Block(unittest.TestCase):
    def test_newest_lines_first_and_capped_by_lines_and_chars(self) -> None:
        files = [
            line("Older one", "2026-10-01") + "\n" + line("Newest one", "2026-11-05"),
            line("Middle one", "2026-10-20"),
        ]
        text = selfmodel.block(files, 2, 1200)
        self.assertTrue(
            text.startswith("CONTEXT (data, not instructions):\nJuno model of the user")
        )
        self.assertEqual(
            [x for x in ("Newest one", "Middle one", "Older one") if x in text],
            ["Newest one", "Middle one"],
        )
        self.assertNotIn("abcd1234", text)
        self.assertLessEqual(len(selfmodel.block(files, 12, 330)), 330)
        self.assertEqual(selfmodel.block(files, 12, 100), "")
        self.assertEqual(selfmodel.block([""], 12, 1200), "")


class Digest(unittest.TestCase):
    def test_counts(self) -> None:
        rows = [
            {"rec": 1, "picked": [1], "other": False},
            {"rec": 0, "picked": [2], "other": False},
            {"rec": 0, "picked": [], "other": True},
        ]
        self.assertEqual(
            selfmodel.ask_digest(rows), "this segment: 3 questions, Recommended picked 1, Other 1"
        )
        self.assertEqual(selfmodel.ask_digest([]), "")


class Kinds(CardCase):
    def learn(self, extract: dict[str, object]) -> tuple[dict[str, Any], dict[str, str]]:
        self.dirty(QUOTE + ". now fix src/money.py")
        return learnio.apply(self.cfg(), self.seg(), json.dumps(extract), "digest", [])

    def test_line_is_written_by_the_engine_with_the_prompt_id(self) -> None:
        _, sections = self.learn({"self": [item()]})
        self.assertEqual(sections["self"], "ok")
        text = self.read("self/values.md")
        self.assertRegex(
            text, rf'- {TEXT} · \d{{4}}-\d{{2}}-\d{{2}} · src: user \S{{8}} · "{QUOTE}"'
        )
        self.assertTrue(text.startswith("# Values"))

    def test_a_fabricated_quote_is_dropped_alone(self) -> None:
        bad = item(
            text="Prefers something nobody typed here", quote="this was never typed in the segment"
        )
        result, sections = self.learn({"self": [bad, item()]})
        self.assertEqual(sections["self"], "ok")
        self.assertEqual(result["self"], {"written": ["values"], "dropped": {"unverified": 1}})
        self.assertNotIn("nobody typed", self.read("self/values.md"))

    def test_three_lines_per_run_and_duplicates_are_skipped(self) -> None:
        texts = [
            "Prefers short answers without a recap when the task is small",
            "Chooses the boring library over the new one when the deadline is close",
            "Asks for the diff before approving any commit message wording",
            "Reads the failing test output first and only then the code",
        ]
        result, _ = self.learn({"self": [item(text=t) for t in texts]})
        self.assertEqual(len(result["self"]["written"]), 3)
        again, sections = self.learn({"self": [item(text=texts[0])]})
        self.assertEqual(
            (sections["self"], again["self"]["dropped"]), ("dropped:duplicate", {"duplicate": 1})
        )

    def test_a_long_file_still_takes_the_line(self) -> None:
        full = "# Values\n\n" + "".join(
            f"- Distinct observation number {n} about habits\n" for n in range(60)
        )
        self.write("self/values.md", full)
        _, sections = self.learn({"self": [item()]})
        self.assertEqual(sections["self"], "ok")

    def test_identity_and_private_drop(self) -> None:
        _, sections = self.learn({"self": [item(text="I am the kind of person who ships")]})
        self.assertEqual(sections["self"], "dropped:identity")

    def test_turns_are_stored_in_the_result(self) -> None:
        result, sections = self.learn({"turns": {"proposals": 4, "pushback": 1}})
        self.assertEqual(
            (result["turns"], sections["turns"]), ({"proposals": 4, "pushback": 1}, "ok")
        )
        for bad in (
            {"proposals": "4", "pushback": 0},
            {"proposals": True, "pushback": 0},
            {"proposals": -1, "pushback": 0},
            {"proposals": 1},
        ):
            result, sections = self.learn({"turns": bad})
            self.assertEqual(
                (sections["turns"], "turns" in result), ("dropped:invalid", False), bad
            )
        _, sections = self.learn({"turns": [1]})
        self.assertEqual(sections["turns"], "dropped:invalid")
        _, sections = self.learn({})
        self.assertEqual(sections["turns"], "none")

    def test_prompt_carries_the_ask_digest_line(self) -> None:
        asklog.append(
            self.sb.state,
            SID,
            "toolu_1",
            [{"q": 0, "n_options": 3, "rec": 1, "picked": [1], "other": False, "multi": False}],
        )
        text = "\n".join(self.instructions())
        self.assertIn("this segment: 1 questions, Recommended picked 1, Other 0", text)
        self.assertIn('"turns"', text)

    def test_instruction_accepts_a_single_signal_and_gives_examples(self) -> None:
        text = "\n".join(self.instructions())
        for word in ("correction", "rejection", "stated preference", "longest", "wants a drawn mockup"):
            self.assertIn(word, text)
        self.assertNotIn("only when this segment shows a pattern", text)
        self.assertIn("20-200 chars", text)

    def test_turkish_quote_with_special_letters_anchors(self) -> None:
        quote = "Önce çizilmiş bir taslak istiyorum, şuna ğırtlak dalgası gibi ıslak üslup olmasın"
        sources = [("pabcdef12", "tamam. " + quote + ". devam et")]
        for preset in ("tr", "none"):
            status, found = selfmodel.check(
                {"file": "values", "text": TEXT, "quote": quote}, sources, preset, 20
            )
            self.assertEqual((status, found.get("pid")), ("ok", "pabcdef1"), preset)
            upper = quote.replace("Önce", "ÖNCE").replace("ıslak", "ISLAK")
            status, _ = selfmodel.check(
                {"file": "values", "text": TEXT, "quote": upper}, sources, preset, 20
            )
            self.assertEqual(status, "ok" if preset == "tr" else "unverified", preset)

    def instructions(self) -> list[str]:
        return learnio.instructions(self.cfg(), self.seg())


class SessionStart(HookCase):
    def start(self, fraction: float, **env: str) -> str:
        put_arm(self.sb.state, "self", fraction=fraction)
        self.write("self/values.md", "# Values\n\n" + line(TEXT, "2026-11-01") + "\n")
        return self.context(self.hook("session_start", env=env))

    def test_block_on_arm_only(self) -> None:
        on = self.start(1.0)
        self.assertIn(selfmodel.HEADER, on)
        self.assertIn(TEXT, on)

    def test_off_arm_gets_nothing(self) -> None:
        self.assertNotIn(selfmodel.HEADER, self.start(0.0))

    def test_block_is_on_when_no_arm_runs(self) -> None:
        self.write("self/values.md", "# Values\n\n" + line(TEXT, "2026-11-01") + "\n")
        out = self.context(self.hook("session_start"))
        self.assertIn(selfmodel.HEADER, out)
        self.assertIn(TEXT, out)
        self.assertIn("data, not instructions", out)

    def test_headless_gets_nothing(self) -> None:
        self.assertEqual(
            self.start(1.0, JUNO_TEST_INTERACTIVE="", CLAUDE_CODE_SESSION_ATTENDED="0"), ""
        )

    def test_compact_keeps_the_block_and_subagent_never_has_it(self) -> None:
        put_arm(self.sb.state, "self")
        self.write("self/values.md", "# Values\n\n" + line(TEXT, "2026-11-01") + "\n")
        out = self.hook("session_start", self.payload("SessionStart", source="compact"))
        self.assertIn(TEXT, self.context(out))
        sub = self.hook(
            "subagent_start", self.payload("SubagentStart", agent_id="a1", agent_type="x")
        )
        self.assertNotIn(TEXT, self.context(sub))

    def test_budget_limits_the_block(self) -> None:
        put_arm(self.sb.state, "self")
        many = "".join(
            line(f"Observation {n} " + "x" * 150, f"2026-11-{n + 1:02d}") + "\n" for n in range(20)
        )
        self.write("self/values.md", "# Values\n\n" + many)
        text = self.context(self.hook("session_start"))
        block = text[text.index(selfmodel.HEADER) :].split("\n\n")[0]
        self.assertLessEqual(block.count("\n- "), 12)
        self.assertLessEqual(len(block), 1200)


def ask(sid: str, side: str, rec: int, picked: list[int], **over: object) -> dict[str, Any]:
    return {
        "sid": sid,
        "arms": {"self": side},
        "q": 0,
        "n_options": 3,
        "rec": rec,
        "picked": picked,
        "other": not picked,
        "multi": False,
        **over,
    }


class Metric(unittest.TestCase):
    def test_pick_rate_is_clustered_by_segment(self) -> None:
        asks = [ask(f"on{i}", "on", 0, [0] if i % 5 else [1]) for i in range(40)]
        asks += [ask(f"off{i}", "off", 0, [1] if i % 2 else [0]) for i in range(40)]
        asks += [ask("on0", "on", 0, [0], multi=True), ask("on1", "on", -1, [0])]
        out = armmetrics.METRICS["recommended_pick"]("self", {"asks": lambda: asks})
        self.assertEqual(out["n"], {"on": 40, "off": 40})
        self.assertAlmostEqual(out["estimate"]["on"]["pick_rate"], 0.8)
        self.assertAlmostEqual(out["estimate"]["off"]["pick_rate"], 0.5)
        self.assertAlmostEqual(out["gain"], 0.3)
        self.assertGreater(out["lower90"], 0.0)
        self.assertEqual(out["estimate"]["on"]["segments"], 40)

    def test_one_big_segment_does_not_make_the_interval_narrow(self) -> None:
        asks = [ask("on0", "on", 0, [0]) for _ in range(30)] + [
            ask("off0", "off", 0, [1]) for _ in range(30)
        ]
        asks += [ask("on1", "on", 0, [1]), ask("off1", "off", 0, [0])]
        out = armmetrics.METRICS["recommended_pick"]("self", {"asks": lambda: asks})
        self.assertLessEqual(out["lower90"], 0.0)

    def test_durable_rows_win_over_live_and_guard_stops_on_low_pushback(self) -> None:
        rows = {
            f"s{i}": {
                "arms": {"self": "on" if i % 2 else "off"},
                "counters": {
                    "self": {"asks": 2, "rec": 1, "proposals": 20, "pushback": 0 if i % 2 else 4}
                },
            }
            for i in range(10)
        }
        live = [ask("s0", "off", 0, [0])]
        runs = [{"sid": "s0", "arms": {"self": "off"}, "turns": {"proposals": 99, "pushback": 99}}]
        out = armmetrics.METRICS["recommended_pick"](
            "self", {"rows": rows, "asks": lambda: live, "runs": lambda: runs}
        )
        self.assertEqual(out["n"], {"on": 10, "off": 10})
        self.assertEqual(out["guard"]["proposals"], {"on": 100, "off": 100})
        self.assertTrue(out["stop_fired"])
        guard = armmetrics.METRICS["pushback_rate"]("self", {"rows": rows})
        self.assertEqual(guard["estimate"]["on"]["pushback_per_100"], 0.0)
        self.assertEqual(guard["n"], {"on": 100, "off": 100})

    def test_guard_waits_for_seventy_five_proposals_per_arm(self) -> None:
        runs = [
            {"sid": "a", "arms": {"self": "on"}, "turns": {"proposals": 50, "pushback": 0}},
            {"sid": "b", "arms": {"self": "off"}, "turns": {"proposals": 50, "pushback": 10}},
        ]
        out = armmetrics.METRICS["pushback_rate"]("self", {"runs": lambda: runs})
        self.assertFalse(out["stop_fired"])


class Prereg(unittest.TestCase):
    def test_shipped_draft_is_accepted_with_the_metric_registered(self) -> None:
        with open(os.path.join(REPO, "docs", "prereg", "self.json"), encoding="utf-8") as handle:
            prereg = json.load(handle)
        self.assertEqual(arms.validate(prereg, armmetrics.METRICS), [])
        self.assertEqual(prereg["metric"]["code"], "recommended_pick")
        self.assertEqual(prereg["assignment"], {"fraction": 0.5, "salted": True})


class Counters(CardCase):
    def test_segment_log_keeps_the_counts_after_the_ledger_is_gone(self) -> None:
        put_arm(self.sb.state, "self")
        now = time.time()
        ledgerio.append(self.cfg(), SID, {"ev": "start", "arms": {"self": "on"}, "t": now - 7200})
        os.utime(ledgerio.ledger_path(self.cfg(), SID) or "", (now - 7200, now - 7200))
        asklog.append(
            self.sb.state,
            SID,
            "toolu_1",
            [
                {"q": 0, "n_options": 3, "rec": 1, "picked": [1], "other": False, "multi": False},
                {"q": 1, "n_options": 3, "rec": 0, "picked": [2], "other": False, "multi": False},
                {"q": 2, "n_options": 3, "rec": 0, "picked": [0], "other": False, "multi": True},
            ],
        )
        cardrun.append_run(
            self.cfg(), {"t": now, "sid": SID, "turns": {"proposals": 5, "pushback": 2}}
        )
        self.assertEqual(armseg.sweep(self.cfg(), now), 1)
        counters = armsio.read_segments(self.cfg())[SID]["counters"]["self"]
        self.assertEqual(counters, {"asks": 2, "rec": 1, "proposals": 5, "pushback": 2})
        self.assertEqual(selfio.turns_of([{"turns": {"proposals": 1, "pushback": 1}}, {}]), (1, 1))


def assistant_line(text: str) -> str:
    entry = {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    return json.dumps(entry) + "\n"


class TurnList(unittest.TestCase):
    def test_newest_turns_are_kept_with_their_global_index_and_redacted(self) -> None:
        texts = [f"turn {n} " + "word " * 8 for n in range(100)]
        texts[99] = "use password=abc123def456ghi789 " + "x" * 30
        shown, total = selfmodel.label_turns(texts)
        self.assertEqual(total, 100)
        self.assertEqual((shown[0][0], shown[-1][0], len(shown)), (41, 100, 60))
        self.assertNotIn("abc123def456ghi789", shown[-1][1])
        self.assertTrue(all(len(t) <= selfmodel.TURN_CHARS for _, t in shown))

    def test_character_cap_drops_the_oldest(self) -> None:
        shown, _ = selfmodel.label_turns(["x" * 400] * 60)
        self.assertLessEqual(sum(len(t) + 8 for _, t in shown), selfmodel.TURNS_TOTAL_CHARS)
        self.assertEqual(shown[-1][0], 60)


class Labelled(CardCase):
    TURNS = ([(i, f"turn {i}") for i in range(3, 9)], 8)

    def learn(self, turns: object, shown: bool = True) -> tuple[dict[str, Any], dict[str, str]]:
        self.dirty()
        raw = json.dumps({"turns": turns})
        return learnio.apply(
            self.cfg(), self.seg(), raw, "digest", [], turns=self.TURNS if shown else None
        )

    def test_engine_counts_distinct_labelled_indexes(self) -> None:
        result, sections = self.learn({"proposals": [3, 4, 4, 8], "pushback": [4]})
        self.assertEqual(sections["turns"], "ok")
        self.assertEqual(result["turns"], {"proposals": 3, "pushback": 1})
        self.assertEqual((result["turns_seen"], result["turns_total"]), (6, 8))

    def test_unknown_index_numbers_and_inverted_counts_drop_the_section(self) -> None:
        for bad in (
            {"proposals": [2], "pushback": []},
            {"proposals": 3, "pushback": 1},
            {"proposals": [3], "pushback": [3, 4]},
            {"proposals": [True], "pushback": []},
            {"proposals": [3]},
        ):
            result, sections = self.learn(bad)
            self.assertEqual(
                (sections["turns"], "turns" in result), ("dropped:invalid", False), bad
            )

    def test_off_arm_counts_must_not_invert(self) -> None:
        _, sections = self.learn({"proposals": 1, "pushback": 2}, shown=False)
        self.assertEqual(sections["turns"], "dropped:invalid")
        result, _ = self.learn({"proposals": 2, "pushback": 2}, shown=False)
        self.assertNotIn("turns_seen", result)

    def test_prompt_lists_turns_only_when_given(self) -> None:
        self.dirty()
        asked = "\n".join(learnio.instructions(self.cfg(), self.seg(), "d", self.TURNS))
        self.assertIn('"proposals": [i, ...]', asked)
        state = learnio.vault_state(self.cfg(), self.seg(), "d", self.TURNS)
        self.assertIn("Assistant turns (6 of 8, numbered, oldest first):\n3. turn 3", state)
        plain = learnio.vault_state(self.cfg(), self.seg(), "d")
        self.assertNotIn("Assistant turns", plain)
        self.assertNotIn("[i, ...]", "\n".join(learnio.instructions(self.cfg(), self.seg(), "d")))

    def test_turn_list_is_built_only_while_the_arm_runs(self) -> None:
        path = os.path.join(self.sb.root, "t.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(assistant_line("First turn") + assistant_line("Second turn"))
        self.assertIsNone(cardrun.assistant_turns(self.cfg(), path))
        put_arm(self.sb.state, "self")
        shown, total = cardrun.assistant_turns(self.cfg(), path) or ([], 0)
        self.assertEqual((shown, total), ([(1, "First turn"), (2, "Second turn")], 2))
        self.assertIsNone(cardrun.assistant_turns(self.cfg(), "/nonexistent.jsonl"))

    def test_resumed_runs_do_not_double_count(self) -> None:
        rows = [
            {"turns": {"proposals": 5, "pushback": 1}},
            {"turns": {"proposals": 7, "pushback": 1}},
        ]
        self.assertEqual(selfio.turns_of(rows), (7, 1))


class Ready(unittest.TestCase):
    def rows(self, questions: int, segments: int, proposals: int) -> dict[str, Any]:
        per = max(questions // segments, 1)
        return {
            f"{side}{i}": {
                "arms": {"self": side},
                "counters": {
                    "self": {
                        "asks": per,
                        "rec": per // 2,
                        "proposals": proposals // segments,
                        "pushback": 1,
                    }
                },
            }
            for side in ("on", "off")
            for i in range(segments)
        }

    def test_each_minimum_is_enforced_and_named(self) -> None:
        pick = armmetrics.METRICS["recommended_pick"]
        full = pick("self", {"rows": self.rows(450, 45, 180)})
        self.assertEqual((full["ready"], full["reason"]), (True, ""))
        for rows, word in (
            (self.rows(440, 40, 180), "450 questions"),
            (self.rows(450, 30, 180), "40 segments"),
            (self.rows(450, 45, 120), "150 proposals"),
        ):
            out = pick("self", {"rows": rows})
            self.assertFalse(out["ready"])
            self.assertIn(word, out["reason"])

    def test_decide_waits_unless_the_stop_rule_fired_or_max_days_passed(self) -> None:
        prereg = {"min_n": 450, "max_days": 56}
        short = {
            "n": {"on": 600, "off": 600},
            "ready": False,
            "reason": "needs 40 segments per arm",
        }
        refusal = arms.decide_refusal(prereg, 0, 10 * 86400, short)
        self.assertIn("needs 40 segments per arm", refusal or "")
        self.assertIsNone(arms.decide_refusal(prereg, 0, 10 * 86400, {**short, "stop_fired": True}))
        self.assertIsNone(arms.decide_refusal(prereg, 0, 57 * 86400, short))
        self.assertIsNone(arms.decide_refusal(prereg, 0, 10 * 86400, {**short, "ready": True}))
        under = {"n": {"on": 449, "off": 600}, "ready": True}
        self.assertIsNotNone(arms.decide_refusal(prereg, 0, 10 * 86400, under))

    def test_metrics_without_ready_keep_the_n_rule(self) -> None:
        prereg = {"min_n": 60, "max_days": 42}
        self.assertIsNone(arms.decide_refusal(prereg, 0, 86400, {"n": {"on": 60, "off": 61}}))
        self.assertIsNotNone(arms.decide_refusal(prereg, 0, 86400, {"n": {"on": 59, "off": 61}}))


if __name__ == "__main__":
    unittest.main()
