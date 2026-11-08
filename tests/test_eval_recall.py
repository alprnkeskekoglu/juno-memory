import json
import os
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from juno import config
from juno.adapters import evalio, shadowio
from juno.core import eval as core
from juno.core import shadow
from tests.test_eval import T0, EvalCase, iso


class RecallProxy(EvalCase):
    def setUp(self) -> None:
        super().setUp()
        self.vault = os.path.realpath(self.sb.vault)
        self.transcripts = os.path.join(self.sb.root, "projects")
        for rel in (
            "tasks/ABC-1.md",
            "knowledge/zebra.md",
            "handoffs/misc.md",
            "core.md",
            "protocol/x.md",
            "rules/global.md",
        ):
            self.write(rel, "# x\n")

    def transcript(self, segment: str, calls: list[tuple[Any, ...]]) -> None:
        directory = os.path.join(self.transcripts, "-p")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, segment + ".jsonl"), "w", encoding="utf-8") as handle:
            for ts, tool, rel, *side in calls:
                path = os.path.join(self.vault, rel)
                use = {"type": "tool_use", "name": tool, "input": {"file_path": path}}
                item = {
                    "type": "assistant",
                    "timestamp": iso(ts),
                    "isSidechain": bool(side),
                    "message": {"content": [use]},
                }
                handle.write(json.dumps(item) + "\n")
            handle.write('not json "tool_use"\n')

    def run_proxy(self, floor: float = 0.0) -> dict[str, Any]:
        return evalio.recall_proxy(self.state, self.vault, self.transcripts, floor=floor)

    def test_hit_silent_and_wrong(self) -> None:
        self.shadow("s1", T0, "zebra question", ["knowledge/zebra.md"])
        self.shadow("s1", T0 + 100, "other question")
        self.shadow("s1", T0 + 200, "third question", ["handoffs/misc.md"])
        self.transcript(
            "s1",
            [
                (T0 + 5, "Read", "knowledge/zebra.md"),
                (T0 + 105, "Read", "knowledge/zebra.md"),
                (T0 + 205, "Read", "knowledge/zebra.md"),
            ],
        )
        m = self.run_proxy()
        self.assertEqual((m["with_evidence"], m["hits"], m["silent"], m["wrong"]), (3, 1, 1, 1))
        self.assertAlmostEqual(m["recall"], 1 / 3)
        self.assertEqual([x["kind"] for x in m["misses"]], ["wrong", "silent"])
        self.assertEqual(m["by_source"]["read"], {"needed": 3, "suggested": 1, "with_packs": 1})

    def test_floor_turns_a_hit_into_a_silent_miss(self) -> None:
        self.shadow("s1", T0, "zebra question", ["knowledge/zebra.md"], score=1.0)
        self.transcript("s1", [(T0 + 5, "Read", "knowledge/zebra.md")])
        self.assertEqual(self.run_proxy(floor=0.5)["hits"], 1)
        m = self.run_proxy(floor=2.0)
        self.assertEqual((m["hits"], m["silent"]), (0, 1))

    def test_protocol_rules_written_and_subagent_reads_are_not_evidence(self) -> None:
        self.shadow("s1", T0, "write the card")
        self.transcript(
            "s1",
            [
                (T0 + 1, "Read", "handoffs/misc.md"),
                (T0 + 2, "Edit", "handoffs/misc.md"),
                (T0 + 3, "Read", "core.md"),
                (T0 + 4, "Read", "protocol/x.md"),
                (T0 + 4, "Read", "rules/global.md"),
                (T0 + 5, "Read", "knowledge/zebra.md", "sidechain"),
            ],
        )
        m = self.run_proxy()
        self.assertEqual((m["entries"], m["with_evidence"], m["recall"]), (1, 0, None))

    def test_reads_outside_window_or_vault_do_not_count(self) -> None:
        self.shadow("s1", T0 + 100, "late prompt")
        self.transcript(
            "s1", [(T0 + 50, "Read", "knowledge/zebra.md"), (T0 + 150, "Read", "../outside.md")]
        )
        self.assertEqual(self.run_proxy()["with_evidence"], 0)

    def test_ticket_with_task_file_is_evidence(self) -> None:
        self.shadow("s2", T0, "/plan abc-1 and ABC-2", ["tasks/ABC-1.md"])
        self.shadow("s2", T0 + 10, "back to ABC-1")
        m = self.run_proxy()
        self.assertEqual((m["with_evidence"], m["hits"], m["no_transcript"]), (2, 1, 2))
        self.assertEqual(m["misses"][0]["needed"], ["tasks/ABC-1.md"])
        self.assertEqual(m["by_source"]["ticket"], {"needed": 2, "suggested": 1, "with_packs": 1})

    def test_report_text(self) -> None:
        self.shadow("s2", T0, "about ABC-1")
        report = core.recall_report(self.run_proxy())
        self.assertIn("recall proxy (search only): 0.000 (0/1)", report)
        self.assertIn("recall proxy (search + packs): 0.000 (0/1)", report)
        self.assertIn("1 with nothing suggested", report)
        self.assertIn("needed tasks/ABC-1.md", report)

    def test_pack_delivery_counts_only_for_pack_recall(self) -> None:
        self.shadow("s3", T0, "/maestro:perform ABC-1", ["handoffs/misc.md"])
        self.shadow("s3", T0 + 10, "/maestro:perform ABC-1", delivered=["tasks/ABC-1.md"])
        self.shadow("s3", T0 + 20, "again ABC-1", carried=["tasks/ABC-1.md"])
        m = self.run_proxy()
        self.assertEqual(
            (m["with_evidence"], m["hits"], m["pack_hits"], m["with_pack_data"]), (3, 0, 2, 3)
        )
        self.assertEqual((m["recall"], m["pack_recall"]), (0.0, 2 / 3))
        self.assertEqual([x["kind"] for x in m["misses"]], ["wrong"])
        self.assertEqual(m["by_source"]["ticket"], {"needed": 3, "suggested": 0, "with_packs": 2})

    def test_ticket_only_in_paste_is_its_own_miss_class(self) -> None:
        old = {
            "ts": T0,
            "segment": "s4",
            "prompt": '<pasted_content id="1">\nlog for ABC-1\n</pasted_content> check',
            "suggestions": [{"path": "handoffs/misc.md", "heading": "", "score": 3.0}],
        }
        os.makedirs(self.state, exist_ok=True)
        with open(shadowio.shadow_path(self.state), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(old) + "\n")
        self.shadow("s4", T0 + 10, "look at this log", pasted=["abc-1"])
        self.shadow("s4", T0 + 20, '<pasted_content id="2">ABC-1</pasted_content> and ABC-1 typed')
        m = self.run_proxy()
        self.assertEqual((m["with_evidence"], m["paste"], m["silent"], m["wrong"]), (3, 2, 1, 0))
        self.assertEqual([x["kind"] for x in m["misses"]], ["silent", "paste", "paste"])
        self.assertEqual(m["with_pack_data"], 2)
        self.assertIn("2 with the ticket only in pasted text", core.recall_report(m))

    def test_read_only(self) -> None:
        self.shadow("s1", T0, "zebra")
        self.transcript("s1", [(T0 + 1, "Read", "knowledge/zebra.md")])
        before = sorted(str(p) for p in Path(self.sb.root).rglob("*"))
        self.run_proxy()
        self.assertEqual(sorted(str(p) for p in Path(self.sb.root).rglob("*")), before)

    def test_empty_log_is_a_value_not_an_error(self) -> None:
        m = self.run_proxy()
        self.assertEqual((m["entries"], m["recall"], m["misses"]), (0, None, []))


class CardAb(EvalCase):
    def shadow(self, segment: str, ts: int, prompt: str, arm: str | None = None) -> None:
        entry = shadow.build_entry(ts, segment, prompt, [])
        shadowio.append(self.state, {**entry, "arm": arm} if arm else entry)

    def transcript(self, segment: str, reads: list[tuple[Any, ...]]) -> None:
        directory = os.path.join(self.sb.root, "claude-home", "projects", "-p")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, segment + ".jsonl"), "w", encoding="utf-8") as handle:
            for ts, rel, *side in reads:
                path = os.path.join(os.path.realpath(self.vault), rel)
                use = {"type": "tool_use", "name": "Read", "input": {"file_path": path}}
                item = {
                    "type": "assistant",
                    "timestamp": iso(ts),
                    "isSidechain": bool(side),
                    "message": {"content": [use]},
                }
                handle.write(json.dumps(item) + "\n")

    def test_ab_compares_arms(self) -> None:
        self.write("handoffs/misc.md", "# x\n")
        self.shadow("h1", T0, "no, that is wrong", arm="holdout")
        self.shadow("h1", T0 + 10, "continue", arm="holdout")
        self.shadow("h2", T0, "hello", arm="holdout")
        self.shadow("c1", T0, "hello", arm="cards")
        self.shadow("x1", T0, "no arm")
        self.transcript("h1", [(T0 + 1, "handoffs/misc.md")])
        self.transcript("h2", [(T0 + 1, "handoffs/misc.md", "sidechain")])
        projects = os.path.join(self.sb.root, "claude-home", "projects")
        before = sorted(os.listdir(self.state))
        data = evalio.card_ab(self.state, self.vault, projects, ["no,", "wrong"], "none")
        self.assertEqual(data["segments"], 3)
        hold, cards = data["arms"]["holdout"], data["arms"]["cards"]
        self.assertEqual(
            (hold["segments"], hold["prompts"], hold["cues"], hold["card_reads"]), (2, 3, 1, 1)
        )
        self.assertEqual((hold["prompts_per_segment"], hold["card_read_share"]), (1.5, 0.5))
        self.assertEqual(
            (cards["segments"], cards["no_transcript"], cards["card_read_share"]), (1, 1, None)
        )
        self.assertEqual(sorted(os.listdir(self.state)), before)

    def test_default_cues_come_from_the_config(self) -> None:
        self.shadow("h1", T0, "don't do that", arm="cards")
        with mock.patch("juno.adapters.evalio.config.load", return_value=config.load(self.sb.env)):
            data = evalio.card_ab(self.state, self.vault, os.path.join(self.sb.root, "none"))
        self.assertEqual(data["arms"]["cards"]["cues"], 1)


class Cli(EvalCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("notes/zebra.md", "# zebra\n\nzebra stripes migration notes\n")
        self.eval_dir = os.path.join(self.state, "eval")
        self.projects = os.path.join(self.sb.root, "claude-home", "projects")
        self.sb.env["JUNO_CLAUDE_PROJECTS"] = self.projects

    def good(self, i: int) -> str:
        return f"tell me about zebra stripes migration {i}"

    def set_entry(self, i: int, hard: bool = False) -> dict[str, Any]:
        prompt = f"qqqxyz wwwvvv {i}" if hard else self.good(i)
        sugg = [] if hard else [{"path": "notes/zebra.md", "heading": "zebra", "score": 5.0}]
        return {
            "id": f"e{i}{'h' if hard else 'u'}",
            "ts": 1,
            "segment": "s",
            "prompt": prompt,
            "suggestions": sugg,
            "label": None if hard else "useful",
            "prefill": None,
            "forbidden": [],
            "hard_negative": hard,
        }

    def make_cli_set(self, name: str, useful: int = 5, hard: int = 3) -> str:
        os.makedirs(self.eval_dir, exist_ok=True)
        path = os.path.join(self.eval_dir, name)
        entries = [self.set_entry(i) for i in range(useful)]
        entries += [self.set_entry(i, True) for i in range(hard)]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"v": 1, "seed": 0, "entries": entries}, handle)
        return path

    def frozen_floor(self, **sizes: int) -> tuple[str, float]:
        path = self.make_cli_set("set-1.json", **sizes)
        self.assertEqual(self.sb.cli("eval", "freeze", path)[0], 0)
        code, out = self.sb.cli("eval", "floor", path)
        self.assertEqual(code, 0, out)
        return path, float(out.strip())

    def fill(self, count: int) -> None:
        for i in range(count):
            self.shadow(f"seg{i % 2}", 1000 + i, self.good(i))

    def test_sample_writes_set_file_and_json(self) -> None:
        self.fill(4)
        code, out = self.sb.cli("eval", "sample", "--n", "3")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip(), os.path.join(self.eval_dir, "set-1.json"))
        self.assertEqual(len(json.loads(Path(out.strip()).read_text())["entries"]), 3)
        self.assertFalse(os.path.exists(os.path.join(self.vault, "eval")))
        code, out = self.sb.cli("eval", "sample", "--json")
        self.assertEqual((json.loads(out)["entries"], code), (4, 0))

    def test_sample_since_and_exclude_set(self) -> None:
        today = time.strftime("%Y-%m-%d")
        day = int(time.mktime(time.strptime(today, "%Y-%m-%d")))
        self.shadow("s", day - 60, "old prompt")
        self.shadow("s", day + 60, "new prompt")
        code, out = self.sb.cli("eval", "sample", "--since", today)
        self.assertEqual(code, 0, out)
        prompts = [e["prompt"] for e in json.loads(Path(out.strip()).read_text())["entries"]]
        self.assertEqual(prompts, ["new prompt"])
        self.assertEqual(self.sb.cli("eval", "sample", "--since", "25.10.2026")[0], 2)
        self.fill(4)
        first = self.sb.cli("eval", "sample", "--n", "2")[1].strip()
        code, out = self.sb.cli("eval", "sample", "--n", "2", "--exclude-set", first)
        self.assertEqual(code, 0, out)

        def ids(path: str) -> set[str]:
            return {e["id"] for e in json.loads(Path(path).read_text())["entries"]}

        self.assertEqual(ids(first) & ids(out.strip()), set())

    def test_freeze_floor_gate_on_fixture_writes_last_gate(self) -> None:
        self.frozen_floor()
        path2 = self.make_cli_set("set-2.json", useful=5, hard=20)
        self.assertEqual(self.sb.cli("eval", "freeze", path2)[0], 0)
        floor = self.sb.cli("eval", "floor", path2)[1].strip()
        code, out = self.sb.cli("eval", "gate", path2, "--floor", floor)
        self.assertEqual(code, 0, out)
        self.assertIn("gate: PASS", out)
        code, out = self.sb.cli("eval", "gate", path2, "--floor", floor, "--json")
        self.assertEqual((code, json.loads(out)["passed"]), (0, True))
        last = json.loads(Path(self.eval_dir, "last-gate.json").read_text())
        self.assertEqual((last["passed"], last["set"]), (True, "set-2.json"))

    def test_gate_fails_with_few_hard_negatives(self) -> None:
        path, floor = self.frozen_floor(hard=3)
        code, out = self.sb.cli("eval", "gate", path, "--floor", str(floor))
        self.assertEqual(code, 1, out)
        self.assertIn("gate: FAIL", out)
        self.assertIn("only 3 hard negatives, need >= 20", out)
        last = json.loads(Path(self.eval_dir, "last-gate.json").read_text())
        self.assertFalse(last["passed"])

    def test_tampered_and_unfrozen_sets_are_refused(self) -> None:
        path, floor = self.frozen_floor()
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(" ")
        for argv in (("floor", path), ("gate", path, "--floor", str(floor))):
            self.assertEqual(self.sb.cli("eval", *argv)[0], 2)
        unfrozen = self.make_cli_set("set-9.json")
        self.assertEqual(self.sb.cli("eval", "floor", unfrozen)[0], 2)

    def test_empty_shadow_log_and_missing_set_are_clean_errors(self) -> None:
        self.assertEqual(self.sb.cli("eval", "sample")[0], 2)
        self.assertEqual(
            self.sb.cli("eval", "freeze", os.path.join(self.eval_dir, "nope.json"))[0], 2
        )

    def test_freeze_refuses_set_inside_vault(self) -> None:
        source = self.make_cli_set("set-1.json")
        inside = self.write("eval/set-1.json", Path(source).read_text())
        link = os.path.join(self.sb.root, "vault-link")
        os.symlink(os.path.dirname(inside), link)
        for path in (inside, os.path.join(link, "set-1.json")):
            self.assertEqual(self.sb.cli("eval", "freeze", path)[0], 2)
        self.assertEqual(os.listdir(os.path.dirname(inside)), ["set-1.json"])
        self.assertEqual(self.sb.cli("eval", "freeze", source)[0], 0)

    def test_agree_recall_and_ab_verbs(self) -> None:
        pairs = [("useful", "useful")] * 3 + [("not", "useful")]
        entries = [
            {**self.set_entry(i), "label": a, "prefill": b} for i, (a, b) in enumerate(pairs)
        ]
        path = os.path.join(self.sb.root, "agree.json")
        Path(path).write_text(json.dumps({"entries": entries}))
        code, out = self.sb.cli("eval", "agree", path, "--json")
        self.assertEqual((code, json.loads(out)["n"]), (0, 4))
        self.write("tasks/ABC-1.md", "# ABC-1\n")
        self.shadow("s1", T0, "about ABC-1", ["tasks/ABC-1.md"], score=1.0)
        self.shadow("s1", T0 + 10, "back to ABC-1")
        code, out = self.sb.cli("eval", "recall")
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("# Juno recall proxy at floor 0.0\n"))
        self.assertIn("recall proxy (search only): 0.500 (1/2)", out)
        code, out = self.sb.cli("eval", "recall", "--floor", "2", "--json")
        data = json.loads(out)
        self.assertEqual(
            (data["floor"], data["hits"], data["silent"], data["no_transcript"]), (2.0, 0, 2, 2)
        )
        code, out = self.sb.cli("eval", "ab")
        self.assertEqual((code, out.strip()), (0, "no arms"))


if __name__ == "__main__":
    unittest.main()
