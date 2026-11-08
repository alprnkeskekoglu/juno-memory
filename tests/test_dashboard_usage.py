import json
import os
import time
import unittest

from juno.core import dashboard_scan as scan
from juno.core import dashboard_usage as usage
from juno.core.dashboard_prices import PRICES
from tests.support import SandboxCase

NOW_LOCAL = "2026-10-25 12:00:00"


def local_epoch(text: str) -> float:
    return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))


def line(
    mid: str,
    ts: str,
    model: str = "claude-opus-4-7",
    inp: int = 0,
    out: int = 0,
    read: int = 0,
    w5: int = 0,
    w1: int = 0,
    sid: str = "s1",
    cwd: str = "/work/app",
) -> str:
    counts = {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": w5 + w1,
        "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1},
    }
    row = {
        "type": "assistant",
        "timestamp": ts,
        "sessionId": sid,
        "cwd": cwd,
        "isSidechain": False,
        "message": {"id": mid, "model": model, "usage": counts},
    }
    return json.dumps(row) + "\n"


class UsageCase(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Istanbul"
        time.tzset()
        self.addCleanup(self.restore_tz)
        self.root = os.path.join(self.sb.root, "transcripts")
        os.makedirs(os.path.join(self.root, "proj"))
        self.now = local_epoch(NOW_LOCAL)

    def restore_tz(self) -> None:
        if self.old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.old_tz
        time.tzset()

    def transcript(self, name: str, *lines: str, mode: str = "w") -> str:
        path = os.path.join(self.root, "proj", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode, encoding="utf-8") as handle:
            handle.write("".join(lines))
        return path

    def aggregate(self) -> tuple[dict, dict]:
        cache, stats = scan.collect(self.sb.state, self.root, self.now)
        return usage.aggregate(cache, PRICES, self.now, "/home/x"), stats


class Scan(UsageCase):
    def test_dedupe_by_message_id_keeps_max_output(self) -> None:
        self.transcript(
            "a.jsonl",
            line("m1", "2026-10-25T08:00:00Z", out=5),
            line("m1", "2026-10-25T08:00:01Z", out=20),
            line("m1", "2026-10-25T08:00:02Z", out=7),
            line("m2", "2026-10-25T08:01:00Z", out=1),
        )
        agg, _ = self.aggregate()
        self.assertEqual((agg["today"]["calls"], agg["today"]["output_tokens"]), (2, 21))

    def test_dedupe_across_files(self) -> None:
        row = line("m1", "2026-10-25T08:00:00Z", out=9)
        self.transcript("a.jsonl", row)
        self.transcript("b.jsonl", row)
        self.assertEqual(self.aggregate()[0]["today"]["calls"], 1)

    def test_incremental_scan_reads_only_new_bytes(self) -> None:
        self.transcript("a.jsonl", line("m1", "2026-10-25T08:00:00Z", out=1))
        cache, first = scan.collect(self.sb.state, self.root, self.now)
        scan.save_cache(self.sb.state, cache)
        self.assertEqual(first["scanned"], 1)
        _, idle = scan.collect(self.sb.state, self.root, self.now)
        self.assertEqual((idle["scanned"], idle["bytes"]), (0, 0))
        extra = line("m2", "2026-10-25T08:05:00Z", out=2)
        self.transcript("a.jsonl", extra, mode="a")
        cache, again = scan.collect(self.sb.state, self.root, self.now)
        self.assertEqual(
            (again["scanned"], again["rescanned"], again["bytes"]), (1, 0, len(extra.encode()))
        )
        self.assertEqual(len(cache["msgs"]), 2)

    def test_partial_last_line_waits_for_its_newline(self) -> None:
        full = line("m2", "2026-10-25T08:05:00Z", out=2)
        self.transcript("a.jsonl", line("m1", "2026-10-25T08:00:00Z", out=1), full[:40])
        cache, _ = scan.collect(self.sb.state, self.root, self.now)
        scan.save_cache(self.sb.state, cache)
        self.assertEqual(len(cache["msgs"]), 1)
        self.transcript("a.jsonl", full[40:], mode="a")
        cache, stats = scan.collect(self.sb.state, self.root, self.now)
        self.assertEqual((len(cache["msgs"]), stats["rescanned"]), (2, 0))

    def test_shrunk_file_and_changed_mtime_rescan(self) -> None:
        path = self.transcript(
            "a.jsonl", *[line(f"m{i}", "2026-10-25T08:00:00Z", out=1) for i in range(5)]
        )
        cache, _ = scan.collect(self.sb.state, self.root, self.now)
        scan.save_cache(self.sb.state, cache)
        self.transcript("a.jsonl", line("n1", "2026-10-25T09:00:00Z", out=3))
        cache, stats = scan.collect(self.sb.state, self.root, self.now)
        self.assertEqual(stats["rescanned"], 1)
        self.assertIn("n1", cache["msgs"])
        scan.save_cache(self.sb.state, cache)
        info = os.stat(path)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 5_000_000_000))
        self.assertEqual(scan.collect(self.sb.state, self.root, self.now)[1]["rescanned"], 1)

    def test_old_files_and_corrupt_lines_are_skipped(self) -> None:
        old = self.transcript("old.jsonl", line("m1", "2026-08-01T08:00:00Z", out=1))
        os.utime(old, (self.now - 40 * 86400,) * 2)
        self.transcript(
            "a.jsonl",
            "not json {\n",
            '{"type":"assistant","usage":1}\n',
            "[1]\n",
            line("m1", "2026-10-25T08:00:00Z", out=4),
        )
        agg, stats = self.aggregate()
        self.assertEqual((stats["files"], agg["today"]["calls"]), (1, 1))

    def test_subagent_and_project_split(self) -> None:
        self.transcript("a.jsonl", line("m1", "2026-10-25T08:00:00Z", out=1, cwd="/work/app"))
        self.transcript(
            "a/subagents/agent-1.jsonl",
            line("m2", "2026-10-25T08:00:00Z", out=1, cwd="/home/x/lib"),
        )
        agg, _ = self.aggregate()
        self.assertEqual(
            {k["name"]: k["calls"] for k in agg["window"]["by_kind"]}, {"main": 1, "subagent": 1}
        )
        self.assertEqual({p["name"] for p in agg["window"]["by_project"]}, {"/work/app", "~/lib"})


class Pricing(UsageCase):
    def test_cost_formula_with_all_token_kinds(self) -> None:
        self.transcript(
            "a.jsonl",
            line("m1", "2026-10-25T08:00:00Z", inp=1000, out=1000, read=1000, w5=1000, w1=1000),
        )
        self.assertAlmostEqual(self.aggregate()[0]["today"]["cost"], 0.04675, places=4)

    def test_dated_and_bracket_model_ids_match(self) -> None:
        for name in (
            "claude-sonnet-4-6",
            "claude-sonnet-4-6-20260101",
            "claude-opus-5[1m]",
            "claude-opus-5-5-20260301[1m]",
        ):
            self.assertIsNotNone(usage.match_price(PRICES, name), name)
        self.assertIsNone(usage.match_price(PRICES, "claude-sonnet-4"))

    def test_unpriced_model_is_counted_not_costed(self) -> None:
        self.transcript(
            "a.jsonl",
            line("m1", "2026-10-25T08:00:00Z", model="claude-unknown-9", inp=100, out=50),
            line("m2", "2026-10-25T08:00:00Z", inp=1000000),
        )
        agg, _ = self.aggregate()
        self.assertEqual(
            agg["unpriced"], [{"model": "claude-unknown-9", "calls": 1, "tokens": 150}]
        )
        self.assertAlmostEqual(agg["today"]["cost"], 5.0, places=4)

    def test_empty_synthetic_message_is_skipped(self) -> None:
        self.transcript(
            "a.jsonl",
            line("m1", "2026-10-25T08:00:00Z", model="<synthetic>"),
            line("m2", "2026-10-25T08:00:00Z", model="<synthetic>", out=3),
        )
        agg, _ = self.aggregate()
        self.assertEqual(agg["unpriced"], [{"model": "<synthetic>", "calls": 1, "tokens": 3}])

    def test_haiku_tier_applies_per_call_above_100k(self) -> None:
        entry = usage.match_price(PRICES, "claude-haiku-5-5")
        assert entry is not None
        low = usage.call_cost(PRICES, entry, (1000, 1000, 0, 0, 0))
        high = usage.call_cost(PRICES, entry, (150000, 1000, 0, 0, 0))
        self.assertAlmostEqual(low, (1000 * 0.10 + 1000 * 0.50) / 1e6)
        self.assertAlmostEqual(high, (150000 * 0.50 + 1000 * 2.50) / 1e6)

    def test_explicit_cache_read_price_wins_over_default(self) -> None:
        entry = usage.match_price(PRICES, "claude-opus-5-5")
        assert entry is not None
        self.assertAlmostEqual(usage.call_cost(PRICES, entry, (0, 0, 1_000_000, 0, 0)), 0.20)

    def test_prices_override_file_and_corrupt_file(self) -> None:
        os.makedirs(self.sb.state)
        path = os.path.join(self.sb.state, "prices.json")
        with open(path, "w", encoding="utf-8") as h:
            json.dump({"models": {"x": {"in": 1, "out": 1}}}, h)
        self.assertIn("x", scan.load_prices(self.sb.state)["models"])
        with open(path, "w", encoding="utf-8") as h:
            h.write("{broken")
        with self.assertRaises(ValueError):
            scan.load_prices(self.sb.state)

    def test_share_of_calls_above_200k_context(self) -> None:
        self.transcript(
            "a.jsonl",
            line("m1", "2026-10-25T08:00:00Z", inp=250000, out=1),
            line("m2", "2026-10-25T08:00:00Z", inp=1000, out=1),
        )
        over = self.aggregate()[0]["window"]["over_200k"]
        self.assertEqual((over["calls"], over["share"]), (1, 0.5))


class Days(UsageCase):
    def test_local_day_differs_from_utc_at_2230z(self) -> None:
        epoch = usage.iso_epoch("2026-10-25T22:30:00Z")
        self.assertEqual(usage.local_day(epoch), "2026-10-26")

    def test_windows_are_whole_local_days_including_today(self) -> None:
        days = usage.window_days(7, self.now)
        self.assertEqual((len(days), days[-1], days[0]), (7, "2026-10-25", "2026-10-19"))
        self.transcript(
            "a.jsonl", line("m1", "2026-10-19T00:30:00+00:00".replace("+00:00", "Z"), out=1)
        )
        agg, _ = self.aggregate()
        self.assertEqual(agg["window"]["calls"], 1)


if __name__ == "__main__":
    unittest.main()
