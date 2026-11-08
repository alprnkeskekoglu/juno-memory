import json
import os
import time

from juno.adapters import armseg, armsio, learnio, ledgerio, nextio
from juno.adapters.learnkinds import next as nextkind
from juno.adapters.learnkinds.base import Ctx
from juno.adapters.vaultview import DiskVault
from juno.core import armmetrics, arms
from juno.handlers import delivery
from tests.support import REPO
from tests.support.cardcase import KEY, SEG8, CardCase, card
from tests.test_arms import put_arm

DIGEST = "Commands: pytest tests/test_money.py passed\n"
PAIRS = [
    {"q": "Which test covers the money rounding?", "a": "tests/test_money.py, it passed."},
    {"q": "What is left to do?", "a": "Open the pull request."},
]


def start_next(case: CardCase, fraction: float = 1.0) -> None:
    put_arm(case.sb.state, "next", fraction=fraction)


class Prereg(CardCase):
    def test_shipped_draft_is_accepted_with_the_metric_registered(self) -> None:
        with open(os.path.join(REPO, "docs", "prereg", "next.json"), encoding="utf-8") as handle:
            prereg = json.load(handle)
        self.assertEqual(arms.validate(prereg, armmetrics.METRICS), [])
        self.assertEqual(prereg["metric"]["code"], "next_lookup_calls")


class Kind(CardCase):
    def learn(self, *pairs: object, running: bool = True) -> dict[str, str]:
        if running:
            start_next(self)
        self.dirty()
        raw = json.dumps({"next": list(pairs)})
        return learnio.apply(self.cfg(), self.seg(), raw, DIGEST, ["handoffs/x.md"])[1]

    def test_instructions_only_while_the_arm_runs(self) -> None:
        ctx = Ctx(self.cfg(), self.seg())
        self.assertEqual(nextkind.instructions(ctx), [])
        self.assertNotIn('"next"', "\n".join(learnio.instructions(self.cfg(), self.seg(), DIGEST)))
        start_next(self)
        self.assertEqual(len(nextkind.instructions(ctx)), 1)
        self.assertIn('"next"', "\n".join(learnio.instructions(self.cfg(), self.seg(), DIGEST)))

    def test_stored_by_seg8_and_file_not_in_the_vault(self) -> None:
        self.assertEqual(self.learn(*PAIRS)["next"], "ok")
        (row,) = [json.loads(x) for x in open(nextio.path(self.cfg()), encoding="utf-8")]
        self.assertEqual((row["seg"], row["file"], row["pairs"]), (SEG8, "handoffs/x.md", PAIRS))
        self.assertEqual(nextio.load(self.cfg()), {SEG8: PAIRS})

    def test_nothing_is_stored_without_the_arm(self) -> None:
        self.assertEqual(self.learn(*PAIRS, running=False)["next"], "none")
        self.assertFalse(os.path.exists(nextio.path(self.cfg())))

    def test_bad_shapes_and_secrets_are_dropped(self) -> None:
        self.assertEqual(self.learn(PAIRS[0])["next"], "dropped:invalid")
        self.assertEqual(self.learn(*PAIRS, PAIRS[0], PAIRS[1])["next"], "dropped:invalid")
        long_q = {"q": "x" * 121, "a": "fine"}
        self.assertEqual(self.learn(PAIRS[0], long_q)["next"], "dropped:invalid")
        self.assertEqual(self.learn(PAIRS[0], {"q": "why"})["next"], "dropped:invalid")
        secret = {"q": "What is the password?", "a": "password=abc123def456ghi789"}
        self.assertEqual(self.learn(PAIRS[0], secret)["next"], "dropped:private")
        self.assertFalse(os.path.exists(nextio.path(self.cfg())))

    def test_block_is_capped(self) -> None:
        text = nextio.block([{"q": "q" * 120, "a": "a" * 200}] * 3)
        self.assertLessEqual(len(text), nextio.BLOCK_CHARS)
        self.assertEqual(text.count("->"), 1)


class Delivery(CardCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("handoffs/2026-01.md", "# Cards\n\n" + card())
        nextio.append(self.cfg(), SEG8, "handoffs/2026-01.md", PAIRS)

    def deliver(self) -> tuple[str, dict[str, object]]:
        cfg = self.cfg()
        self.calls_made = getattr(self, "calls_made", 0) + 1
        sid = f"deliver-sid-{self.calls_made}"
        seg = self.seg(sid)
        text = delivery.deliver_packs(cfg, DiskVault(cfg), seg, [("ticket", KEY)], 6000)
        pack = [r for r in self.ledger_events(sid) if r.get("ev") == "pack"]
        return text, pack[-1]

    def ledger_events(self, sid: str) -> list[dict[str, object]]:
        with open(ledgerio.ledger_path(self.cfg(), sid) or "", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def test_on_arm_shows_and_off_arm_holds_and_no_arm_is_silent(self) -> None:
        text, event = self.deliver()
        self.assertNotIn("[Next", text)
        self.assertNotIn("next_shown", event)
        self.assertNotIn("next_held", event)

        start_next(self, 1.0)
        text, event = self.deliver()
        self.assertIn(f"[Next · {SEG8}]\n- Which test covers the money rounding? ->", text)
        self.assertEqual(event["next_shown"], SEG8)
        self.assertNotIn("next_held", event)

    def test_off_arm_gets_nothing_but_logs_held(self) -> None:
        start_next(self, 0.0)
        text, event = self.deliver()
        self.assertNotIn("[Next", text)
        self.assertEqual(event["next_held"], SEG8)
        self.assertNotIn("next_shown", event)

    def test_card_without_stored_questions_logs_nothing(self) -> None:
        start_next(self, 1.0)
        self.write("handoffs/2026-01.md", "# Cards\n\n" + card(seg="other123"))
        text, event = self.deliver()
        self.assertNotIn("[Next", text)
        self.assertNotIn("next_shown", event)


class Metric(CardCase):
    def test_prediction_hit_uses_the_first_three_prompts(self) -> None:
        ask = ["Which test covers the money rounding?"]
        self.assertTrue(
            armmetrics.predicted(ask, ["x", "which test covers money rounding"], "none")
        )
        self.assertFalse(
            armmetrics.predicted(ask, ["a", "b", "c", "which test covers money rounding"], "none")
        )

    def test_metric_compares_lookups_per_unit(self) -> None:
        def row(side: str, lookups: int, hit: int) -> dict[str, object]:
            counters = {"next": {"units": 1, "lookups": lookups, "hit": hit}}
            return {"arms": {"next": side}, "counters": counters}

        rows = {f"on{i}": row("on", 1, 1) for i in range(20)}
        rows.update({f"off{i}": row("off", 3, 0) for i in range(20)})
        rows["none"] = {"arms": {"next": "on"}, "counters": {"next": {"units": 0}}}
        out = armmetrics.METRICS["next_lookup_calls"]("next", {"rows": rows})
        self.assertEqual(out["n"], {"on": 20, "off": 20})
        self.assertEqual(out["estimate"]["on"]["lookups_per_unit"], 1)
        self.assertEqual(out["estimate"]["off"]["hit_rate"], 0)
        self.assertEqual(out["diff_upper90"], -2)
        self.assertFalse(out["stop_fired"])

    def test_counters_are_written_per_segment_row(self) -> None:
        start_next(self)
        cfg = self.cfg()
        self.prompt("which test covers the money rounding", "s1")
        ledgerio.append(cfg, "s1", {"ev": "start", "arms": {"next": "on"}, "t": 1.0})
        ledgerio.append(
            cfg, "s1", {"ev": "pack", "key": "ticket:X", "next_shown": SEG8, "t": time.time()}
        )
        nextio.append(cfg, SEG8, "", PAIRS)
        counts = armseg.next_counters(cfg, "s1", "next")
        self.assertEqual(counts, {"units": 1, "hit": 1})
        self.assertEqual(armseg.next_counters(cfg, "none", "next"), {"units": 0})
        self.assertIn("next", armseg.COUNTERS)
        self.assertEqual(set(armsio.running(cfg)), {"next"})
