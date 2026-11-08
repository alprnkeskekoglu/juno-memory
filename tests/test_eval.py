import datetime
import json
import os
import stat
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from juno.adapters import evalio, index, shadowio
from juno.core import eval as core
from juno.core import shadow
from tests.support import SandboxCase

T0 = 1_800_000_000


def iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).isoformat().replace("+00:00", "Z")


class EvalCase(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.state = self.sb.state
        self.vault = self.sb.vault
        os.makedirs(self.vault, exist_ok=True)

    def shadow(
        self, segment: str, ts: int, prompt: str, paths: Any = (), score: float = 3.0, **kw: Any
    ) -> None:
        sugg = [{"path": p, "score": score} for p in paths]
        shadowio.append(self.state, shadow.build_entry(ts, segment, prompt, sugg, **kw))

    def write(self, rel: str, text: str) -> str:
        path = os.path.join(self.vault, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def make_set(self, entries: list[dict[str, Any]], name: str = "set-1.json") -> str:
        directory = os.path.join(self.sb.root, "sets")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"v": 1, "entries": entries}, handle)
        evalio.freeze_set(path)
        return path

    def entry(self, **kw: Any) -> dict[str, Any]:
        base = {
            "id": "x",
            "prompt": "alpha",
            "suggestions": [],
            "label": "useful",
            "prefill": None,
            "forbidden": [],
            "hard_negative": False,
        }
        base.update(kw)
        return base


class Sampling(EvalCase):
    def fill(self) -> None:
        for seg in ("a", "b", "c"):
            for i in range(12):
                self.shadow(seg, 1000 + i, f"prompt {seg} {i}")

    def test_deterministic_and_stratified(self) -> None:
        self.fill()
        s1 = evalio.sample_for_labelling(self.state, n=9, seed=7)
        s2 = evalio.sample_for_labelling(self.state, n=9, seed=7)
        self.assertEqual([e["id"] for e in s1], [e["id"] for e in s2])
        self.assertEqual(sorted(e["segment"] for e in s1), ["a"] * 3 + ["b"] * 3 + ["c"] * 3)
        s3 = evalio.sample_for_labelling(self.state, n=9, seed=8)
        self.assertNotEqual([e["id"] for e in s1], [e["id"] for e in s3])
        self.assertIsNone(s1[0]["label"])
        self.assertIsNone(s1[0]["prefill"])
        self.assertEqual(s1[0]["forbidden"], [])

    def test_exclude_ids(self) -> None:
        self.fill()
        ids = [e["id"] for e in evalio.sample_for_labelling(self.state, n=10, seed=1)]
        again = evalio.sample_for_labelling(self.state, n=10, seed=1, exclude_ids=ids)
        self.assertFalse(set(ids) & {e["id"] for e in again})

    def test_sets_never_overwritten(self) -> None:
        self.fill()
        s1 = evalio.sample_for_labelling(self.state, n=5, seed=1)
        s2 = evalio.sample_for_labelling(self.state, n=5, seed=2)
        self.assertTrue(s1.path.endswith("set-1.json") and s2.path.endswith("set-2.json"))
        before = Path(s1.path).read_bytes()
        with self.assertRaises(FileExistsError):
            evalio.sample_for_labelling(self.state, n=5, seed=3, k=1)
        self.assertEqual(Path(s1.path).read_bytes(), before)
        self.assertEqual(stat.S_IMODE(os.stat(s1.path).st_mode), 0o600)

    def test_empty_shadow_fails_loudly(self) -> None:
        with self.assertRaises(ValueError):
            evalio.sample_for_labelling(self.state)


class Freezing(EvalCase):
    def test_tamper_detected(self) -> None:
        path = self.make_set([self.entry()])
        evalio.load_frozen(path)
        Path(path).write_text(Path(path).read_text().replace("useful", "not"))
        with self.assertRaises(core.SetTamperedError):
            evalio.load_frozen(path)
        with self.assertRaises(core.SetTamperedError):
            evalio.evaluate(path, "/nonexistent.db", 0.0)

    def test_unfrozen_refused_and_no_refreeze(self) -> None:
        path = os.path.join(self.sb.root, "u.json")
        Path(path).write_text(json.dumps({"entries": []}))
        with self.assertRaises(core.SetTamperedError):
            evalio.load_frozen(path)
        evalio.freeze_set(path)
        with self.assertRaises(FileExistsError):
            evalio.freeze_set(path)

    def test_invalid_label_rejected(self) -> None:
        path = os.path.join(self.sb.root, "bad.json")
        Path(path).write_text(json.dumps({"entries": [self.entry(label="maybe")]}))
        with self.assertRaises(ValueError):
            evalio.freeze_set(path)


class Wilson(unittest.TestCase):
    def test_values(self) -> None:
        self.assertAlmostEqual(core.wilson_lower(21, 30), 0.521, places=2)
        self.assertLess(core.wilson_lower(20, 30), 0.5)
        self.assertEqual(core.wilson_lower(0, 0), 0.0)
        self.assertGreater(core.wilson_lower(30, 30), 0.88)


class FloorAndGate(EvalCase):
    def mk(self, prompt: str, label: str | None = None, hn: bool = False) -> dict[str, Any]:
        return self.entry(id=prompt, prompt=prompt, label=label, prefill="useful", hard_negative=hn)

    def synthetic(
        self, n_hn: int = 20, fire_hn: int = 4, hn_score: float = 1.0
    ) -> list[dict[str, Any]]:
        entries = []
        for i in range(10):
            entries.append(self.mk(f"good{i}", "useful"))
            self.table[f"good{i}"] = [(f"notes/g{i}.md", 5.0)]
        for i in range(5):
            entries.append(self.mk(f"meh{i}", "not"))
            self.table[f"meh{i}"] = [(f"notes/m{i}.md", 2.0)]
        for i in range(n_hn):
            entries.append(self.mk(f"neg{i}", "not", hn=True))
            if i < fire_hn:
                self.table[f"neg{i}"] = [(f"notes/n{i}.md", hn_score)]
        return entries

    def setUp(self) -> None:
        super().setUp()
        self.table: dict[str, list[tuple[str, float]]] = {}

    def frozen(self, entries: list[dict[str, Any]]) -> str:
        for e in entries:
            e["suggestions"] = [
                {"path": p, "heading": "", "score": s} for p, s in self.table.get(e["prompt"], [])
            ]
        return self.make_set(entries)

    def patched(self) -> Any:
        return mock.patch.multiple(evalio, timed_runs=self.fake_runs, retired_of=lambda db: set())

    def fake_runs(self, entries: Any, db: str, preset: str) -> list[core.Run]:
        return [(e, core.logged_results(e), 1.0) for e in entries]

    def test_choose_floor(self) -> None:
        path = self.frozen(self.synthetic())
        with self.patched():
            self.assertEqual(evalio.choose_floor(path, "db"), 5.0)

    def test_choose_floor_infeasible(self) -> None:
        path = self.frozen(self.synthetic(fire_hn=20, hn_score=5.0))
        with self.patched(), self.assertRaises(ValueError):
            evalio.choose_floor(path, "db")

    def test_evaluate_ignores_prefill(self) -> None:
        path = self.frozen(self.synthetic())
        with self.patched():
            m, text = evalio.evaluate(path, "db", 2.0)
        self.assertEqual((m["fired"], m["useful"]), (15, 10))
        self.assertAlmostEqual(m["precision"], 10 / 15)
        self.assertEqual(m["abstention"], 1.0)
        self.assertIn("Wilson", text)
        self.assertIsNotNone(m["p95_ms"])

    def test_gate_passes(self) -> None:
        entries = [e for e in self.synthetic() if not e["id"].startswith("meh")]
        for i in range(15):
            entries.append(self.mk(f"extra{i}", "useful"))
            self.table[f"extra{i}"] = [(f"notes/e{i}.md", 5.0)]
        path = self.frozen(entries)
        with self.patched():
            result = evalio.gate(path, "db", 5.0)
        self.assertTrue(result["passed"], result["reasons"])

    def test_gate_fails_with_fewer_than_20_negatives(self) -> None:
        path = self.frozen(self.synthetic(n_hn=19, fire_hn=0))
        with self.patched():
            result = evalio.gate(path, "db", 5.0)
        self.assertFalse(result["passed"])
        self.assertTrue(any("19 hard negatives" in r for r in result["reasons"]))

    def test_gate_fails_on_low_wilson(self) -> None:
        path = self.frozen(self.synthetic())
        with self.patched():
            result = evalio.gate(path, "db", 2.0)
        self.assertFalse(result["passed"])
        self.assertTrue(any("Wilson" in r for r in result["reasons"]))

    def test_unlabelled_fired_fails_gate(self) -> None:
        entries = self.synthetic()
        entries.append(self.mk("unl", None))
        self.table["unl"] = [("notes/u.md", 5.0)]
        path = self.frozen(entries)
        with self.patched():
            result = evalio.gate(path, "db", 5.0)
        self.assertFalse(result["passed"])
        self.assertTrue(any("unlabelled" in r for r in result["reasons"]))


class RealIndex(EvalCase):
    def test_judges_logged_suggestions_not_current_index(self) -> None:
        self.write("notes/later.md", "# Later\n\nzorkplan written after the prompt\n")
        db = os.path.join(self.state, "index.sqlite3")
        index.build_index(self.vault, db)
        path = self.make_set([self.entry(id="a", prompt="zorkplan")])
        m, _ = evalio.evaluate(path, db, 0.0)
        self.assertEqual((m["fired"], m["unlabelled"]), (0, 0))
        self.assertIsNotNone(m["p95_ms"])

    def test_forbidden_and_retired_detected(self) -> None:
        self.write("notes/old.md", "# Old\n\nzorkplan details\n")
        self.write("notes/live.md", "# Live\n\nzorkplan current\n")
        self.write("notes/new.md", "---\nsupersedes: notes/old.md\n---\n# New\n\nzorkplan newest\n")
        db = os.path.join(self.state, "index.sqlite3")
        index.build_index(self.vault, db)
        self.assertIn("notes/old.md", index.retired_paths(db))
        entries = [
            self.entry(
                id="a",
                prompt="zorkplan",
                suggestions=[{"path": "notes/old.md", "heading": "", "score": 9.0}],
            ),
            self.entry(
                id="b",
                prompt="zorkplan",
                suggestions=[{"path": "notes/live.md", "heading": "", "score": 4.0}],
                forbidden=["notes/live.md"],
            ),
            self.entry(id="c", prompt="unrelated qqqq", label="not", hard_negative=True),
        ]
        path = self.make_set(entries)
        m, _ = evalio.evaluate(path, db, 0.0)
        got = {(h["id"], h["path"]) for h in m["forbidden_hits"]}
        self.assertEqual(got, {("a", "notes/old.md"), ("b", "notes/live.md")})
        self.assertEqual(m["abstention"], 1.0)
        result = evalio.gate(path, db, 0.0)
        self.assertFalse(result["passed"])
        self.assertTrue(any("forbidden" in r for r in result["reasons"]))


class Agreement(EvalCase):
    def labelled(self, pairs: list[tuple[str | None, str | None]]) -> list[dict[str, Any]]:
        return [self.entry(id=f"e{i}", label=a, prefill=b) for i, (a, b) in enumerate(pairs)]

    def test_kappa(self) -> None:
        pairs = [("useful", "useful")] * 4 + [("not", "not")] * 4
        pairs += [("useful", "not"), ("not", "useful"), ("useful", None), (None, None)]
        data = core.agreement(self.labelled(pairs))
        self.assertEqual((data["n"], data["one_side_only"], len(data["disagreements"])), (10, 1, 2))
        self.assertAlmostEqual(data["observed"], 0.8)
        self.assertAlmostEqual(data["expected"], 0.5)
        self.assertAlmostEqual(data["kappa"], 0.6)
        self.assertIn("Cohen's kappa: 0.600", core.agreement_report(data))

    def test_one_class_has_no_kappa(self) -> None:
        data = core.agreement(self.labelled([("useful", "useful")] * 3))
        self.assertEqual((data["observed"], data["kappa"]), (1.0, None))

    def test_no_pairs_or_bad_label(self) -> None:
        with self.assertRaises(ValueError):
            core.agreement(self.labelled([("useful", None)]))
        with self.assertRaises(ValueError):
            core.agreement(self.labelled([("useful", "maybe")]))


if __name__ == "__main__":
    unittest.main()
