import json
import os
import re
import sqlite3
import statistics
import time
import unittest
from typing import Any
from unittest import mock

from juno import config
from juno.adapters import index, keysbackfill, keysio, shadowio
from juno.core import cardfmt, shadow
from juno.core import eval as evalcore
from juno.core import keys as keycore
from juno.core import search as core
from tests.support import timing
from tests.support.cardcase import SEG8, CardCase, card, with_extract
from tests.support.hooks import HookCase
from tests.test_index import VaultCase

FACTS = ["canary rollout needs zebrafish approval", "the blue switch flips the pods"]
RAW = "Roll the pods with the blue switch and wait for the green light."
LONG = "Billing runs nightly and writes one ledger row per refund. " * 5


def key_row(h: str, facts: list[str]) -> dict[str, Any]:
    return keysio.row(h, "notes/deploy.md", facts, "backfill", "m")


class Facts(unittest.TestCase):
    def test_hash_is_content_addressed_and_nfc(self) -> None:
        self.assertEqual(keycore.passage_hash("é"), keycore.passage_hash("é"))
        self.assertNotEqual(keycore.passage_hash("a"), keycore.passage_hash("b"))
        self.assertRegex(keycore.passage_hash("a"), r"^[0-9a-f]{16}$")

    def test_check_facts(self) -> None:
        good = ["x" * 10, "y" * 160]
        self.assertEqual(keycore.check_facts(good), (good, "", 0))
        self.assertEqual(keycore.check_facts(["z" * 240]), (["z" * 240], "", 0))
        for bad, why in (
            (["x" * 241], "length"),
            (["a" * 12] * 6 + [1], None),
            ([], "count"),
            ("text", "invalid"),
            (["a fact with a · dot"], "invalid"),
            (["token ghp_" + "a" * 36, "short"], "private"),
            (["only one fine fact", "x" * 161], "length"),
            ([1, 2], "invalid"),
        ):
            got = keycore.check_facts(bad)
            if why is None:
                self.assertEqual(got[0], ["a" * 12] * 5)
            else:
                self.assertEqual(got[:2], (None, why), bad)

    def test_a_secret_in_one_of_four_facts_keeps_the_other_three(self) -> None:
        raw = ["first fact here", "token ghp_" + "a" * 36, "third fact here", "fourth fact here"]
        self.assertEqual(
            keycore.check_facts(raw),
            (["first fact here", "third fact here", "fourth fact here"], "", 1),
        )

    def test_one_over_long_fact_is_dropped_and_the_rest_kept(self) -> None:
        raw = ["first fact here", "x" * 161, "third fact here"]
        self.assertEqual(keycore.check_facts(raw), (["first fact here", "third fact here"], "", 1))

    def test_a_dot_fact_is_dropped(self) -> None:
        raw = ["first fact here", "has a · dot", "third fact here"]
        self.assertEqual(keycore.check_facts(raw), (["first fact here", "third fact here"], "", 1))

    def test_more_than_five_truncates_to_the_first_five_valid(self) -> None:
        raw = [f"fact number {i}" for i in range(7)]
        got = keycore.check_facts(raw)
        self.assertEqual((got[0], got[1], got[2]), (raw[:5], "", 2))

    def test_one_valid_fact_among_several_is_not_a_context_sentence(self) -> None:
        self.assertEqual(
            keycore.check_facts(["a perfectly fine fact", "x" * 200, "nope"]), (None, "length", 2)
        )

    def test_a_lone_context_sentence_up_to_240_is_kept(self) -> None:
        self.assertEqual(keycore.check_facts(["c" * 200]), (["c" * 200], "", 0))

    def test_eligibility(self) -> None:
        long = "w" * 200
        self.assertTrue(keycore.eligible("notes/a.md", {}, "H", long))
        self.assertFalse(keycore.eligible("notes/a.md", {}, "H", "w" * 199))
        for rel in ("rules/global.md", "protocol/x.md", "_generated/learned.md", "core.md"):
            self.assertFalse(keycore.eligible(rel, {}, "H", long), rel)
        self.assertFalse(keycore.eligible("notes/a.md", {"visibility": "private"}, "H", long))
        self.assertFalse(keycore.eligible("notes/a.md", {}, "x · stub · y", long))

    def test_parse_batch(self) -> None:
        answer = '```\n{"1": ["fact number one", "fact number two"], "2": "no", "9": []}\n```'
        got = keycore.parse_batch(answer, ["1", "2", "3"])
        self.assertEqual(
            got, {"1": (["fact number one", "fact number two"], "", 0), "2": (None, "invalid", 0)}
        )
        self.assertEqual(keycore.parse_batch("not json", ["1"]), {})
        self.assertEqual(keycore.parse_batch("[1]", ["1"]), {})

    def test_prompt_redacts_and_fences(self) -> None:
        text = keycore.batch_prompt([("1", "deploy with ghp_" + "a" * 36)], "data-abc")
        self.assertNotIn("ghp_", text)
        self.assertIn("[redacted:github]", text)
        self.assertIn("<data-abc id=1>", text)

    def test_sign_test_is_exact(self) -> None:
        self.assertEqual(keycore.sign_test(0, 0), 1.0)
        self.assertAlmostEqual(keycore.sign_test(5, 0), 0.0625)
        self.assertAlmostEqual(keycore.sign_test(0, 5), 0.0625)
        self.assertEqual(keycore.sign_test(3, 3), 1.0)
        self.assertAlmostEqual(keycore.sign_test(9, 1), 0.021484375)

    def test_paired_counts_cells(self) -> None:
        hits = {0.0: [True, True, False, False, False], 2.0: [True, False, True, True, False]}
        got = keycore.paired(hits)
        self.assertEqual(got["2"], {"only_w": 2, "only_0": 1, "both": 1, "neither": 1, "p": 1.0})


class IndexKeys(VaultCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("notes/deploy.md", f"# Deploy notes\n\n{RAW}\n")
        self.keys_file = str(self.tmp / "state" / "keys.jsonl")
        os.makedirs(os.path.dirname(self.keys_file), exist_ok=True)

    def build_keyed(self) -> dict[str, Any]:
        return index.build_index(str(self.vault), str(self.db), None, self.preset, self.keys_file)

    def add_keys(self, raw: str = RAW, facts: list[str] | None = None) -> None:
        keysio.append(
            os.path.dirname(self.keys_file),
            [key_row(keycore.passage_hash(raw), facts or FACTS)],
        )

    def test_fact_only_term_finds_the_passage_and_the_snippet_is_raw(self) -> None:
        self.build_keyed()
        self.assertEqual(self.paths("zebrafish"), [])
        self.add_keys()
        self.build_keyed()
        (hit,) = self.search("zebrafish")
        self.assertEqual(hit["relpath"], "notes/deploy.md")
        self.assertEqual(hit["snippet"], RAW)
        self.assertNotIn("zebrafish", hit["snippet"])

    def test_incremental_key_update_without_reindex(self) -> None:
        self.build_keyed()
        self.add_keys()
        done = self.build_keyed()
        self.assertEqual((done["scanned"], done["reindexed"]), (1, 0))
        self.assertEqual(self.paths("zebrafish"), ["notes/deploy.md"])
        self.assertEqual(self.build_keyed()["reindexed"], 0)

    def test_a_key_orphaned_by_an_edit_is_not_used(self) -> None:
        self.add_keys()
        self.build_keyed()
        self.assertEqual(self.paths("zebrafish"), ["notes/deploy.md"])
        path = self.write("notes/deploy.md", f"# Deploy notes\n\n{RAW} Edited.\n")
        os.utime(path, ns=(1, 1_700_000_000_000_000_000))
        self.assertEqual(self.build_keyed()["reindexed"], 1)
        self.assertEqual(self.paths("zebrafish"), [])
        self.assertEqual(self.paths("edited"), ["notes/deploy.md"])

    def test_weight_zero_turns_keys_off(self) -> None:
        self.add_keys()
        self.build_keyed()
        self.assertEqual(self.paths("zebrafish", keys_weight=0.0), [])
        self.assertEqual(self.paths("zebrafish", keys_weight=2.0), ["notes/deploy.md"])
        self.assertEqual(self.paths("blue switch", keys_weight=0.0), ["notes/deploy.md"])

    def test_weight_orders_a_key_match_above_a_body_match(self) -> None:
        self.write("notes/body.md", "# Other\n\nzebrafish zebrafish zebrafish appears in body\n")
        self.add_keys()
        self.build_keyed()
        self.assertEqual(self.paths("zebrafish", keys_weight=0.0), ["notes/body.md"])
        self.assertEqual(self.paths("zebrafish", keys_weight=500.0)[0], "notes/deploy.md")

    def test_schema_1_index_is_rebuilt(self) -> None:
        os.makedirs(os.path.dirname(self.db), exist_ok=True)
        conn = sqlite3.connect(self.db)
        conn.executescript(
            "CREATE TABLE files (id INTEGER PRIMARY KEY, relpath TEXT);"
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
            "CREATE VIRTUAL TABLE passages USING fts5(title, heading, body, raw UNINDEXED);"
            "PRAGMA user_version=1;"
        )
        conn.close()
        with self.assertRaises(RuntimeError):
            index.open_readonly(str(self.db)).close()
        self.assertEqual(self.build_keyed()["reindexed"], 1)
        conn = index.open_readonly(str(self.db))
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], index.SCHEMA_VERSION)
        conn.close()
        self.assertEqual(self.paths("pods"), ["notes/deploy.md"])


class Backfill(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.cfg = config.load(dict(self.env))
        self.prompts: list[str] = []
        self.cost = 0.02

    def passages(self, count: int, prefix: str = "item") -> None:
        for i in range(count):
            self.write(f"notes/{prefix}{i}.md", f"# Note {i}\n\n{LONG} variant {prefix}{i}\n")

    def call(self, cfg: config.Config, prompt: str, budget: float, **kw: Any) -> tuple[str, float]:
        self.prompts.append(prompt)
        ids = re.findall(r"id=(\d+)>", prompt)
        facts = {i: [f"fact for passage {i} first", f"fact for passage {i} second"] for i in ids}
        return json.dumps(facts), self.cost

    def keyed(self) -> dict[str, list[str]]:
        return keysio.load(keysio.keys_path(self.cfg.state))

    def test_keys_the_passages_then_a_second_run_calls_no_model(self) -> None:
        self.passages(3)
        out = keysbackfill.backfill(self.cfg, None, 2.0, self.call)
        self.assertEqual((out["keyed"], out["calls"], out["stopped"]), (3, 1, None))
        self.assertEqual(len(self.keyed()), 3)
        again = keysbackfill.backfill(self.cfg, None, 2.0, self.call)
        self.assertEqual((again["calls"], again["pending"], len(self.prompts)), (0, 0, 1))

    def test_max_usd_stops_and_the_next_run_resumes(self) -> None:
        self.passages(45)
        self.cost = 0.3
        out = keysbackfill.backfill(self.cfg, None, 0.5, self.call)
        self.assertEqual((out["calls"], out["stopped"], out["keyed"]), (2, "max-usd", 40))
        self.cost = 0.01
        rest = keysbackfill.backfill(self.cfg, None, 2.0, self.call)
        self.assertEqual((rest["calls"], rest["keyed"], rest["stopped"]), (1, 5, None))
        self.assertEqual(keysbackfill.status(self.cfg)["pending"], 0)

    def test_limit_caps_the_passages(self) -> None:
        self.passages(30)
        out = keysbackfill.backfill(self.cfg, 5, 2.0, self.call)
        self.assertEqual((out["keyed"], out["calls"]), (5, 1))
        self.assertEqual(keysbackfill.status(self.cfg)["pending"], 25)

    def test_skips_short_private_rules_protocol_and_core(self) -> None:
        self.passages(1)
        self.write("notes/short.md", "# Short\n\nTiny passage.\n")
        self.write("notes/secret.md", f"---\nvisibility: private\n---\n# S\n\n{LONG}\n")
        self.write("rules/extra.md", f"# R\n\n{LONG}\n")
        self.write("protocol/p.md", f"# P\n\n{LONG}\n")
        todo, _, _ = keysbackfill.missing(self.cfg)
        self.assertEqual({rel for rel, _, _ in todo}, {"notes/item0.md"})

    def test_the_sent_text_is_redacted(self) -> None:
        secret = "ghp_" + "a" * 36
        self.write("notes/leak.md", f"# Leak\n\n{LONG} token {secret}\n")
        keysbackfill.backfill(self.cfg, None, 2.0, self.call)
        self.assertNotIn(secret, self.prompts[0])
        self.assertIn("[redacted:github]", self.prompts[0])

    def rows(self) -> dict[str, dict[str, Any]]:
        return keysio.load_rows(keysio.keys_path(self.cfg.state))

    def test_unusable_facts_are_recorded_with_a_reason_and_retried_once(self) -> None:
        self.passages(2)

        def call(cfg: config.Config, prompt: str, budget: float, **kw: Any) -> tuple[str, float]:
            self.prompts.append(prompt)
            bad = ["token ghp_" + "b" * 36, "short"]
            good = ["a good fact one", "a good fact two"]
            return json.dumps({"1": bad, "2": good}), 0.02

        out = keysbackfill.backfill(self.cfg, None, 2.0, call)
        self.assertEqual((out["keyed"], out["dropped"]), (1, 1))
        (empty,) = [r for r in self.rows().values() if not r["facts"]]
        self.assertEqual(empty["why"], "private")
        self.assertNotIn("retried", empty)
        self.assertNotIn("ghp_", json.dumps(empty))
        self.assertEqual(
            keysbackfill.status(self.cfg),
            {
                "total": 2,
                "keyed": 1,
                "empty": 1,
                "pending": 0,
                "retry": 1,
                "reasons": {"private": 1},
            },
        )
        again = keysbackfill.backfill(self.cfg, None, 2.0, call)
        self.assertEqual((again["calls"], again["pending"]), (1, 1))
        (empty,) = [r for r in self.rows().values() if not r["facts"]]
        self.assertTrue(empty["retried"])
        self.assertEqual(keysbackfill.status(self.cfg)["retry"], 0)
        self.assertEqual(keysbackfill.backfill(self.cfg, None, 2.0, call)["calls"], 0)
        self.assertEqual(len(self.prompts), 2)

    def test_a_retry_that_succeeds_keys_the_passage(self) -> None:
        self.passages(1)
        answers = iter([["too short"], ["a good fact one", "a good fact two"]])

        def call(cfg: config.Config, prompt: str, budget: float, **kw: Any) -> tuple[str, float]:
            return json.dumps({"1": next(answers)}), 0.02

        keysbackfill.backfill(self.cfg, None, 2.0, call)
        self.assertEqual(self.rows().popitem()[1]["why"], "length")
        keysbackfill.backfill(self.cfg, None, 2.0, call)
        (row,) = self.rows().values()
        self.assertEqual(row["facts"], ["a good fact one", "a good fact two"])
        self.assertTrue(row["retried"])
        self.assertEqual(keysbackfill.status(self.cfg)["keyed"], 1)

    def test_a_legacy_empty_row_has_reason_none_and_is_retried(self) -> None:
        self.passages(1)
        todo, _, _ = keysbackfill.missing(self.cfg)
        ((rel, h, _),) = todo
        with open(keysio.keys_path(self.cfg.state), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"h": h, "rel": rel, "facts": []}) + "\n")
        status = keysbackfill.status(self.cfg)
        self.assertEqual((status["retry"], status["reasons"]), (1, {"none": 1}))
        out = keysbackfill.backfill(self.cfg, None, 2.0, self.call)
        self.assertEqual((out["keyed"], out["calls"]), (1, 1))

    def test_kept_rows_store_the_dropped_count_only(self) -> None:
        self.passages(1)

        def call(cfg: config.Config, prompt: str, budget: float, **kw: Any) -> tuple[str, float]:
            facts = ["fact one is fine", "x" * 161, "fact three is fine"]
            return json.dumps({"1": facts}), 0.02

        keysbackfill.backfill(self.cfg, None, 2.0, call)
        (row,) = self.rows().values()
        self.assertEqual((row["dropped"], len(row["facts"])), (1, 2))
        self.assertNotIn("why", row)
        self.assertNotIn("x" * 161, json.dumps(row))

    def test_a_model_failure_stops_the_run(self) -> None:
        self.passages(25)

        def call(cfg: config.Config, prompt: str, budget: float, **kw: Any) -> tuple[str, float]:
            raise cardfmt.Failed("timeout after 240s", 0.01)

        out = keysbackfill.backfill(self.cfg, None, 2.0, call)
        self.assertEqual((out["calls"], out["keyed"]), (0, 0))
        self.assertTrue(out["stopped"].startswith("model: timeout"))
        self.assertEqual(self.keyed(), {})

    def test_the_default_call_uses_the_fake_claude_with_card_flags(self) -> None:
        from tests.support import fakeclaude

        self.passages(1)
        self.set_config('[keys]\nmodel = "haiku"\n')
        fakeclaude.answer(self.env, json.dumps({"1": ["first fact here", "second fact here"]}))
        with mock.patch.dict(os.environ, self.env):
            out = keysbackfill.backfill(config.load(dict(self.env)), None, 2.0)
        self.assertEqual((out["keyed"], out["model"]), (1, "haiku"))
        argv = fakeclaude.call_log(self.env)["argv"]
        assert isinstance(argv, list)
        self.assertEqual(argv[argv.index("--model") + 1], "haiku")
        self.assertIn("--no-session-persistence", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")


class CardKind(CardCase):
    def keyed(self) -> dict[str, list[str]]:
        return keysio.load(keysio.keys_path(self.cfg().state))

    def card_hashes(self) -> set[str]:
        body = (
            self.handoff().split("\n", 1)[1] if self.handoff().startswith("---") else self.handoff()
        )
        return {
            keycore.passage_hash(raw)
            for head, raw in core.split_passages(body)
            if head.endswith(f" · {SEG8}")
        }

    def test_facts_are_stored_for_the_card_passage(self) -> None:
        self.dirty()
        row = self.run_card(with_extract(card(), {"keys": FACTS}))
        self.assertEqual(self.sections(row)["keys"], "ok")
        stored = self.keyed()
        self.assertEqual(set(stored), self.card_hashes())
        self.assertTrue(stored and all(v == FACTS for v in stored.values()))
        (line,) = open(keysio.keys_path(self.cfg().state), encoding="utf-8").read().splitlines()
        self.assertEqual(json.loads(line)["by"], "card")

    def test_a_secret_fact_drops_only_the_keys(self) -> None:
        self.dirty()
        data = {
            "keys": ["token ghp_" + "c" * 36, "another fine fact"],
            "pitfalls": [
                {
                    "text": "Rounding belongs in the money type, not in the view",
                    "evidence": "src/money.py",
                    "triggers": [],
                }
            ],
        }
        row = self.run_card(with_extract(card(), data))
        sections = self.sections(row)
        self.assertEqual((sections["card"], sections["keys"]), ("ok", "dropped:private"))
        self.assertEqual(sections["pitfalls"], "ok")
        self.assertEqual(self.keyed(), {})

    def test_missing_wrong_shape_and_bad_count(self) -> None:
        self.dirty()
        row = self.run_card(with_extract(card(), {"rules": []}))
        self.assertEqual(self.sections(row)["keys"], "none")
        self.redirty()
        row = self.run_card(with_extract(card(), {"keys": "text"}))
        self.assertEqual(self.sections(row)["keys"], "dropped:invalid")
        self.redirty()
        row = self.run_card(with_extract(card(), {"keys": ["only one", "x" * 11]}))
        self.assertEqual(self.sections(row)["keys"], "dropped:length")
        self.redirty()
        row = self.run_card(with_extract(card(), {"keys": ["fine fact number one"] * 3 + ["bad"]}))
        self.assertEqual(self.sections(row)["keys"], "ok")
        self.assertEqual({len(f) for f in self.keyed().values()}, {3})

    def test_a_failed_card_stores_nothing(self) -> None:
        self.dirty()
        row = self.run_card(with_extract("no heading here\nLearned: x", {"keys": FACTS}))
        self.assertEqual(row["status"], "failed")
        self.assertEqual(self.sections(row)["keys"], "dropped:no card passage")
        self.assertEqual(self.keyed(), {})

    def test_the_prompt_asks_for_keys(self) -> None:
        self.dirty()
        self.run_card(card())
        self.assertIn('- "keys": 2-5 strings', self.calls[0]["prompt"])


class Compare(unittest.TestCase):
    def test_evidence_entries_pair_each_entry_with_what_the_session_opened_next(self) -> None:
        entries = [
            {"ts": 10, "segment": "s1", "prompt": "deploy the pods"},
            {"ts": 50, "segment": "s1", "prompt": "later"},
            {"ts": 10, "segment": "s2", "prompt": "no reads"},
        ]
        touches = {"s1": [(20.0, "Read", "notes/deploy.md"), (60.0, "Read", "notes/late.md")]}
        got = evalcore.evidence_entries(entries, lambda seg: touches.get(seg), lambda rel: False)
        self.assertEqual(
            [(e["prompt"], sorted(n)) for e, n in got],
            [("deploy the pods", ["notes/deploy.md"]), ("later", ["notes/late.md"])],
        )

    def test_report_lists_every_weight(self) -> None:
        result = {
            "setting": "keys",
            "n": 5,
            "hits": {"0": 2, "2": 3},
            "weights": keycore.paired(
                {0.0: [True, True, False, False, False], 2.0: [True, False, True, True, False]}
            ),
        }
        text = evalcore.compare_report(result)
        self.assertIn("5 entries with evidence", text)
        self.assertIn("weight 2: only-2 2, only-0 1, both 1, neither 1, sign test p=1.0", text)


class ShadowAlt(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.set_config("[shadow]\nbudget_ms = 5000\n")
        self.write("knowledge/deploy.md", f"# Deploy\n\n{RAW}\n")
        self.cfg = config.load(dict(self.env))

    def prompt(self) -> dict[str, Any]:
        self.hook("user_prompt_submit", self.payload("UserPromptSubmit", prompt="zebrafish"))
        return shadowio.read(self.sb.state)[0][-1]

    def test_alt_is_omitted_without_keys_and_when_weight_zero_finds_nothing(self) -> None:
        self.set_config("[keys]\nweight = 2.0\n")
        self.cfg = config.load(dict(self.env))
        index.sync(self.cfg)
        self.assertNotIn("alt", self.prompt())
        keysio.append(self.sb.state, [key_row(keycore.passage_hash(RAW), FACTS)])
        index.sync(self.cfg)
        entry = self.prompt()
        self.assertEqual([s["path"] for s in entry["suggestions"]], ["knowledge/deploy.md"])
        self.assertNotIn("alt", entry)

    def test_alt_differs_when_weight_zero_ranks_otherwise(self) -> None:
        self.write("knowledge/body.md", "# Body\n\nzebrafish zebrafish zebrafish in body text\n")
        keysio.append(self.sb.state, [key_row(keycore.passage_hash(RAW), FACTS)])
        self.set_config("[keys]\nweight = 500.0\n")
        index.sync(config.load(dict(self.env)))
        entry = self.prompt()
        self.assertEqual(entry["suggestions"][0]["path"], "knowledge/deploy.md")
        self.assertEqual(entry["alt"][0], "knowledge/body.md")

    def test_build_entry_caps_alt_and_omits_it_when_empty(self) -> None:
        entry = shadow.build_entry(1, "s", "p", [], alt=[f"a{i}.md" for i in range(9)])
        self.assertEqual(entry["alt"], [f"a{i}.md" for i in range(5)])
        self.assertNotIn("alt", shadow.build_entry(1, "s", "p", []))

    @timing
    def test_two_queries_stay_within_the_shadow_budget(self) -> None:
        for i in range(300):
            self.write(f"notes/n{i}.md", f"# N{i}\n\nzebrafish note {i} " + "filler " * 80 + "\n")
        keysio.append(self.sb.state, [key_row(keycore.passage_hash(RAW), FACTS)])
        index.sync(self.cfg)
        db = os.path.join(self.sb.state, "index.sqlite3")
        samples = []
        for _ in range(9):
            started = time.perf_counter()
            found = shadowio.search_hits(db, "zebrafish note", "none", 0.15, False, 2.0, True)
            samples.append((time.perf_counter() - started) * 1000)
            self.assertIsNotNone(found)
        self.assertLess(statistics.median(samples), 150)


class Verbs(HookCase):
    def test_status_dry_run_and_search_use_the_keys(self) -> None:
        self.write("notes/a.md", f"# A\n\n{LONG}\n")
        code, out = self.sb.cli("keys", "status", "--json")
        self.assertEqual((code, json.loads(out)["pending"]), (0, 1))
        code, out = self.sb.cli("keys", "backfill", "--dry-run", "--json")
        data = json.loads(out)
        self.assertEqual((code, data["pending"], data["batches"]), (0, 1, 1))
        self.assertFalse(os.path.exists(keysio.keys_path(self.sb.state)))
        code, out = self.sb.cli("keys", "backfill", "--max-usd", "0")
        self.assertEqual(code, 2, out)

    def test_doctor_warns_below_80_percent_coverage(self) -> None:
        self.set_config("[keys]\nweight = 2.0\n")
        self.write("notes/a.md", f"# A\n\n{LONG}\n")
        done = self.sb.sub("doctor", "--json", *self.sb.install_args())
        warns = [c for c in json.loads(done.stdout)["checks"] if c["name"] == "keys"]
        self.assertEqual([c["status"] for c in warns], ["WARN"], done.stdout[:400])
        keysio.append(self.sb.state, [key_row(keycore.passage_hash(LONG.strip()), FACTS)])
        done = self.sb.sub("doctor", "--json", *self.sb.install_args())
        self.assertEqual([c for c in json.loads(done.stdout)["checks"] if c["name"] == "keys"], [])

    def test_backfill_refuses_in_agent_context(self) -> None:
        done = self.sb.sub("keys", "backfill", "--dry-run", extra_env={"CLAUDE_AGENT_ID": "a1"})
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("agent context", done.stderr)

    def test_compare_needs_a_setting_and_reports_json(self) -> None:
        code, out = self.sb.cli("eval", "compare", "--setting", "keys", "--json")
        data = json.loads(out)
        self.assertEqual((code, data["n"], data["weights"].keys()), (0, 0, {"1", "2", "4"}))
        code, _ = self.sb.cli("eval", "compare", "--setting", "keys", "--weights", "x")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
