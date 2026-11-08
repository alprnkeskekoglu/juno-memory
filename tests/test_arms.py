import json
import os
import subprocess
import time
import unittest
from typing import Any

from juno import config
from juno.adapters import armseg, armsio, cardjob, cardrun, index, ledgerio, shadowio
from juno.core import armmetrics, arms
from tests.support import PYTHON, REPO, clean_env, subproc
from tests.support.cardcase import KEY, CardCase, card
from tests.support.hooks import SID, HookCase
from tests.support.write import WriteCase

PREREG: dict[str, Any] = {
    "hypothesis": "h",
    "assignment": {"fraction": 1.0},
    "unit": "a segment",
    "metric": {"primary": "segments per arm", "code": "segments"},
    "min_n": 2,
    "max_days": 30,
    "stop_rule": "decide at min_n or max_days",
    "decision": "on if on beats off",
}


def prereg(**over: Any) -> dict[str, Any]:
    return {**PREREG, **over}


def put_arm(state: str, name: str, salt: str = "abcd", fraction: float = 1.0, **over: Any) -> None:
    body = {"name": name, "salt": salt, "status": "running", "started": 1, **over}
    body["prereg"] = prereg(assignment={"fraction": fraction})
    os.makedirs(os.path.join(state, "arms"), exist_ok=True)
    with open(os.path.join(state, "arms", f"{name}.json"), "w", encoding="utf-8") as handle:
        json.dump(body, handle)


class Assign(unittest.TestCase):
    def test_stable_and_bounded(self) -> None:
        self.assertEqual(arms.assign("s", "sid", 0.5), arms.assign("s", "sid", 0.5))
        self.assertEqual({arms.assign("s", f"x{i}", 0.0) for i in range(50)}, {"off"})
        self.assertEqual({arms.assign("s", f"x{i}", 1.0) for i in range(50)}, {"on"})

    def test_salts_are_independent(self) -> None:
        cells = {(a, b): 0 for a in ("on", "off") for b in ("on", "off")}
        for i in range(2000):
            sid = f"sid-{i}"
            cells[(arms.assign("salt-one", sid, 0.5), arms.assign("salt-two", sid, 0.5))] += 1
        for count in cells.values():
            self.assertAlmostEqual(count / 2000, 0.25, delta=0.03)


class Validate(unittest.TestCase):
    def test_accepts_the_shape(self) -> None:
        self.assertEqual(arms.validate(PREREG, {"segments"}), [])

    def test_names_every_problem(self) -> None:
        bad = {"assignment": {"fraction": 2}, "metric": {"code": "nope"}, "min_n": 0}
        text = "; ".join(arms.validate(bad, {"segments"}))
        for part in ("hypothesis", "assignment.fraction", "metric.primary", "not registered"):
            self.assertIn(part, text)
        self.assertIn("min_n", text)
        self.assertIn("max_days", text)
        self.assertTrue(arms.validate([]))

    def test_name_pattern(self) -> None:
        self.assertTrue(arms.NAME_RE.match("linked-2"))
        for bad in ("A", "1a", "a", "a_b", "x" * 32, "a.json"):
            self.assertIsNone(arms.NAME_RE.match(bad), bad)

    def test_hash_follows_content(self) -> None:
        self.assertEqual(arms.body_hash(PREREG), arms.body_hash(dict(reversed(PREREG.items()))))
        self.assertNotEqual(arms.body_hash(PREREG), arms.body_hash(prereg(min_n=3)))

    def test_decide_refusal(self) -> None:
        short = {"n": {"on": 1, "off": 5}}
        self.assertIn("min_n", arms.decide_refusal(PREREG, 1000, 1001, short) or "")
        self.assertIsNone(arms.decide_refusal(PREREG, 1000, 1001, {"n": {"on": 2, "off": 2}}))
        self.assertIsNone(arms.decide_refusal(PREREG, 1000, 1001, {**short, "stop_fired": True}))
        self.assertIsNone(arms.decide_refusal(PREREG, 1000, 1000 + 30 * arms.DAY, short))


class Metric(unittest.TestCase):
    def test_segments_counts_per_arm_side(self) -> None:
        def entry(seg: str, prompt: str, side: str | None) -> dict[str, Any]:
            return {"segment": seg, "prompt": prompt, "arms": {"demo": side} if side else {}}

        data = {
            "shadow": [
                entry("a", "no", "on"),
                entry("a", "ok", "on"),
                entry("b", "ok", "off"),
                entry("c", "ok", None),
            ],
            "has_cue": lambda text: text == "no",
        }
        out = armmetrics.METRICS["segments"]("demo", data)
        self.assertEqual(out["n"], {"on": 1, "off": 1})
        self.assertEqual(out["estimate"]["on"]["cues_per_100_prompts"], 50.0)
        self.assertEqual(out["estimate"]["on"]["prompts_per_segment"], 2.0)


class LinkedMetric(unittest.TestCase):
    def data(
        self,
        packs: list[tuple[str, float, list[str]]],
        sides: dict[str, str],
        reads: dict[str, list[tuple[float, str]] | None],
    ) -> dict[str, Any]:
        return {
            "packs": lambda: packs,
            "side_of": sides.get,
            "reads": lambda sid: reads.get(sid),
        }

    def test_share_of_units_with_a_later_read_of_a_neighbour(self) -> None:
        packs = [
            ("a", 10.0, ["x.md"]),
            ("b", 10.0, ["x.md"]),
            ("c", 10.0, ["x.md"]),
            ("d", 10.0, []),
            ("e", 10.0, ["x.md"]),
            ("f", 10.0, ["x.md"]),
        ]
        sides = {"a": "on", "b": "on", "c": "off", "d": "off", "e": "on", "f": "off"}
        reads = {
            "a": [(11.0, "x.md")],
            "b": [(9.0, "x.md"), (12.0, "other.md")],
            "c": [(11.0, "x.md")],
            "d": [(11.0, "x.md")],
            "e": None,
            "f": [],
        }
        out = armmetrics.METRICS["linked_read_share"]("linked", self.data(packs, sides, reads))
        self.assertEqual(out["n"], {"on": 2, "off": 2})
        self.assertEqual(out["estimate"]["on"]["read_share"], 0.5)
        self.assertEqual(out["estimate"]["off"]["read_share"], 0.5)
        self.assertFalse(out["stop_fired"])

    def test_harm_stop_after_thirty_units_per_arm(self) -> None:
        packs = [(f"s{i}", 1.0, ["x.md"]) for i in range(60)]
        sides = {f"s{i}": "on" if i % 2 else "off" for i in range(60)}
        shadow = [
            {"segment": f"s{i}", "prompt": prompt, "arms": {"linked": sides[f"s{i}"]}}
            for i in range(60)
            for prompt in (["no", "ok"] if sides[f"s{i}"] == "on" else ["no", "ok", "ok", "ok"])
        ]
        data = {**self.data(packs, sides, {f"s{i}": [] for i in range(60)}), "shadow": shadow}
        data["has_cue"] = lambda text: text == "no"
        self.assertTrue(armmetrics.METRICS["linked_read_share"]("linked", data)["stop_fired"])
        data["has_cue"] = lambda text: False
        self.assertFalse(armmetrics.METRICS["linked_read_share"]("linked", data)["stop_fired"])


class LinkedPrereg(WriteCase):
    def test_shipped_draft_is_accepted_by_arm_start(self) -> None:
        with open(os.path.join(REPO, "docs", "prereg", "linked.json"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(arms.validate(json.loads(text), armmetrics.METRICS), [])
        path = self.body_file(text, "linked-prereg.json")
        code, out = self.juno("arm", "start", "linked", "--prereg", path)
        self.assertEqual(code, 0, out)
        self.assertEqual(set(armsio.running(self.cfg)), {"linked"})


class LinkedLive(WriteCase):
    def test_status_joins_ledger_nb_with_transcript_reads(self) -> None:
        with open(os.path.join(REPO, "docs", "prereg", "linked.json"), encoding="utf-8") as handle:
            self.body_file(handle.read(), "linked-prereg.json")
        code, out = self.juno(
            "arm", "start", "linked", "--prereg", os.path.join(self.sb.state, "linked-prereg.json")
        )
        self.assertEqual(code, 0, out)
        now = time.time()
        ledgerio.append(self.cfg, self.sid, {"ev": "start", "arms": {"linked": "on"}, "t": now})
        ledgerio.append(
            self.cfg, self.sid, {"ev": "pack", "key": "repo:a", "nb": ["notes/log.md"], "t": now}
        )
        read = {
            "type": "assistant",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now + 60)),
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": os.path.join(self.sb.vault, "notes", "log.md")},
                    }
                ]
            },
        }
        projects = os.path.join(self.sb.root, "claude-projects", "p")
        os.makedirs(projects)
        with open(os.path.join(projects, self.sid + ".jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(read) + "\n")
        env = {"JUNO_CLAUDE_PROJECTS": os.path.join(self.sb.root, "claude-projects")}
        code, out = self.juno("arm", "status", "--json", env=env)
        live = json.loads(out)["arms"][0]["live"]
        self.assertEqual(live["n"], {"on": 1, "off": 0})
        self.assertEqual(live["estimate"]["on"]["read_share"], 1.0)


class SegmentLog(LinkedLive):
    def setUp(self) -> None:
        super().setUp()
        self.now = time.time()
        self.old = self.now - 7200

    def seed(self, sid: str, side: str = "on", read: bool = True) -> None:
        ledgerio.append(self.cfg, sid, {"ev": "start", "arms": {"linked": side}, "t": self.old})
        ledgerio.append(
            self.cfg, sid, {"ev": "pack", "key": "repo:a", "nb": ["notes/log.md"], "t": self.old}
        )
        os.utime(ledgerio.ledger_path(self.cfg, sid) or "", (self.old, self.old))
        if read:
            self.transcript(sid)

    def transcript(self, sid: str) -> None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(self.old + 60))
        part = {
            "type": "tool_use",
            "name": "Read",
            "input": {"file_path": os.path.join(self.sb.vault, "notes", "log.md")},
        }
        row = {"type": "assistant", "timestamp": stamp, "message": {"content": [part]}}
        folder = os.path.join(self.sb.root, "claude-projects", "p")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, sid + ".jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

    def start_linked(self) -> None:
        put_arm(self.sb.state, "linked")
        path = os.path.join(self.sb.state, "arms", "linked.json")
        with open(path, encoding="utf-8") as handle:
            body = json.load(handle)
        body["prereg"]["metric"]["code"] = "linked_read_share"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(body, handle)

    def test_nothing_is_written_while_no_arm_runs(self) -> None:
        self.seed("s1")
        self.assertEqual(armseg.sweep(self.cfg, self.now), 0)
        self.assertFalse(os.path.exists(armsio.segments_path(self.cfg)))

    def test_row_is_written_once_and_a_resumed_ledger_replaces_it(self) -> None:
        self.env["JUNO_CLAUDE_PROJECTS"] = os.path.join(self.sb.root, "claude-projects")
        self.start_linked()
        self.seed("s1")
        self.assertEqual(armseg.sweep(self.cfg, self.now), 1)
        self.assertEqual(armseg.sweep(self.cfg, self.now), 0)
        row = armsio.read_segments(self.cfg)["s1"]
        self.assertEqual(row["arms"], {"linked": "on"})
        self.assertEqual(row["counters"]["linked"], {"nb_units": 1, "nb_read": 1})
        self.assertNotIn("notes/log.md", json.dumps(row))
        ledgerio.append(self.cfg, "s1", {"ev": "pack", "key": "repo:b", "nb": ["x.md"], "t": 1.0})
        os.utime(ledgerio.ledger_path(self.cfg, "s1") or "", (self.old + 5, self.old + 5))
        self.assertEqual(armseg.sweep(self.cfg, self.now), 1)
        self.assertEqual(armsio.read_segments(self.cfg)["s1"]["counters"]["linked"]["nb_units"], 2)
        self.assertEqual(len(armsio.read_segments(self.cfg)), 1)

    def test_recent_ledger_waits_unless_forced(self) -> None:
        self.start_linked()
        self.seed("s1", read=False)
        os.utime(ledgerio.ledger_path(self.cfg, "s1") or "", (self.now, self.now))
        self.assertEqual(armseg.sweep(self.cfg, self.now), 0)
        self.assertEqual(armseg.sweep(self.cfg, self.now, force=True), 1)

    def test_metric_uses_the_log_after_the_ledger_and_transcript_are_gone(self) -> None:
        self.env["JUNO_CLAUDE_PROJECTS"] = os.path.join(self.sb.root, "claude-projects")
        self.start_linked()
        self.seed("s1", "on")
        self.seed("s2", "off", read=False)
        self.assertEqual(armseg.sweep(self.cfg, self.now), 2)
        for sid in ("s1", "s2"):
            os.unlink(ledgerio.ledger_path(self.cfg, sid) or "")
        os.unlink(os.path.join(self.sb.root, "claude-projects", "p", "s1.jsonl"))
        _, out = self.juno(
            "arm",
            "status",
            "--json",
            env={"JUNO_CLAUDE_PROJECTS": self.env["JUNO_CLAUDE_PROJECTS"]},
        )
        live = json.loads(out)["arms"][0]["live"]
        self.assertEqual(live["n"], {"on": 1, "off": 0})

    def test_log_is_pruned_with_the_retention_window(self) -> None:
        self.env["JUNO_CLAUDE_PROJECTS"] = os.path.join(self.sb.root, "claude-projects")
        self.start_linked()
        self.seed("s1")
        armseg.sweep(self.cfg, self.now)
        self.assertEqual(armsio.prune_segments(self.cfg, 365, self.now), 0)
        self.assertEqual(armsio.prune_segments(self.cfg, 1, self.now + 3 * 86400), 1)
        self.assertEqual(armsio.read_segments(self.cfg), {})


class Cli(WriteCase):
    def start(self, name: str = "demo", **over: Any) -> tuple[int, str]:
        path = self.body_file(json.dumps(prereg(**over)), f"{name}-prereg.json")
        return self.juno("arm", "start", name, "--prereg", path)

    def stored(self, name: str = "demo") -> dict[str, Any]:
        with open(os.path.join(self.sb.state, "arms", f"{name}.json"), encoding="utf-8") as handle:
            data: dict[str, Any] = json.load(handle)
            return data

    def shadow(self, segment: str, prompt: str, side: str) -> None:
        entry = {"ts": int(time.time()), "segment": segment, "prompt": prompt, "suggestions": []}
        shadowio.append(self.sb.state, {**entry, "arms": {"demo": side}})

    def test_start_writes_salt_hash_and_log_and_consumes_the_file(self) -> None:
        path = self.body_file(json.dumps(PREREG), "p.json")
        code, out = self.juno("arm", "start", "demo", "--prereg", path)
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(path))
        data = self.stored()
        self.assertRegex(data["salt"], r"^[0-9a-f]{16}$")
        self.assertEqual((data["status"], data["sha256"]), ("running", arms.body_hash(PREREG)))
        with open(os.path.join(self.sb.state, "arms", "log.jsonl"), encoding="utf-8") as handle:
            self.assertEqual([json.loads(x)["ev"] for x in handle], ["start"])
        self.assertEqual(armsio.running(self.cfg)["demo"], (data["salt"], 1.0))

    def test_third_running_arm_is_refused(self) -> None:
        self.assertEqual(self.start("one")[0], 0)
        self.assertEqual(self.start("two")[0], 0)
        code, out = self.start("three")
        self.assertEqual(code, 2)
        self.assertTrue(out.startswith("Juno: refused: "), out)
        self.assertEqual(out.count("\n"), 1)
        self.assertEqual(self.start("one")[0], 2)

    def test_bad_name_and_bad_prereg_are_refused(self) -> None:
        self.assertEqual(self.start("Bad_Name")[0], 2)
        code, out = self.start("demo", min_n=0)
        self.assertEqual(code, 2)
        self.assertIn("min_n", out)
        path = self.body_file("not json", "bad.json")
        self.assertEqual(self.juno("arm", "start", "demo", "--prereg", path)[0], 2)
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "arms", "demo.json")))

    def test_start_and_stop_refuse_outside_an_interactive_session(self) -> None:
        env = {"JUNO_TEST_INTERACTIVE": "0", "CLAUDE_CODE_SESSION_ATTENDED": "0"}
        path = self.body_file(json.dumps(PREREG), "p.json")
        code, out = self.juno("arm", "start", "demo", "--prereg", path, env=env)
        self.assertEqual(code, 2)
        self.assertIn("not an interactive session", out)
        self.assertEqual(self.start()[0], 0)
        self.assertEqual(self.juno("arm", "stop", "demo", "--reason", "enough", env=env)[0], 2)

    def test_stop_ends_assignment_and_is_logged(self) -> None:
        self.start()
        code, out = self.juno("arm", "stop", "demo", "--reason", "harm seen early")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.stored()["status"], "stopped")
        self.assertEqual(armsio.running(self.cfg), {})
        self.assertEqual(self.juno("arm", "stop", "demo", "--reason", "again")[0], 2)
        self.assertEqual(self.start("demo")[0], 2)

    def test_decide_refused_before_min_n_then_writes_result_once(self) -> None:
        self.start()
        code, out = self.juno("arm", "decide", "demo")
        self.assertEqual(code, 2)
        self.assertIn("min_n 2 per arm not reached", out)
        for seg, side in (("s1", "on"), ("s2", "on"), ("s3", "off"), ("s4", "off")):
            self.shadow(seg, "hello", side)
        code, out = self.juno("arm", "decide", "demo", "--json")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(out)["n"], {"off": 2, "on": 2})
        self.assertEqual(self.stored()["status"], "decided")
        self.assertEqual(self.stored()["result"]["n"], {"off": 2, "on": 2})
        self.assertEqual(self.juno("arm", "decide", "demo")[0], 2)

    def test_tampered_prereg_is_refused_at_decide(self) -> None:
        self.start()
        data = self.stored()
        data["prereg"]["min_n"] = 1
        with open(os.path.join(self.sb.state, "arms", "demo.json"), "w", encoding="utf-8") as h:
            json.dump(data, h)
        code, out = self.juno("arm", "decide", "demo")
        self.assertEqual(code, 2)
        self.assertIn("changed after start", out)

    def test_status_text_json_and_eval_ab_alias(self) -> None:
        self.assertEqual(self.juno("arm", "status")[1].strip(), "no arms")
        self.start()
        self.shadow("s1", "hello", "on")
        data = json.loads(self.juno("arm", "status", "--json")[1])["arms"][0]
        self.assertEqual(
            (data["name"], data["status"], data["live"]["n"]["on"]), ("demo", "running", 1)
        )
        code, out = self.juno("eval", "ab")
        self.assertEqual(code, 0, out)
        self.assertIn("demo: running", out)


class Logged(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.set_config("[shadow]\nbudget_ms = 5000\n")
        self.write("knowledge/zebra.md", "# Zebra\n\nzebra stripes migration notes\n")
        self.cfg = config.load(self.env)

    def test_start_event_and_shadow_entry_carry_arms(self) -> None:
        index.sync(self.cfg)
        put_arm(self.sb.state, "demo", fraction=1.0)
        env = {"JUNO_TEST_INTERACTIVE": "1"}
        self.hook("session_start", self.payload("SessionStart", source="startup"), env=env)
        self.hook("user_prompt_submit", self.payload("UserPromptSubmit", prompt="zebra stripes"))
        ledger = os.path.join(self.sb.state, "ledgers", SID + ".jsonl")
        with open(ledger, encoding="utf-8") as handle:
            start = next(r for r in map(json.loads, handle) if r["ev"] == "start")
        self.assertEqual(start["arms"], {"demo": "on"})
        self.assertEqual(shadowio.read(self.sb.state)[0][0]["arms"], {"demo": "on"})

    def test_nothing_is_added_while_no_arm_runs(self) -> None:
        index.sync(self.cfg)
        self.hook("session_start", self.payload("SessionStart"), env={"JUNO_TEST_INTERACTIVE": "1"})
        self.hook("user_prompt_submit", self.payload("UserPromptSubmit", prompt="zebra stripes"))
        ledger = os.path.join(self.sb.state, "ledgers", SID + ".jsonl")
        with open(ledger, encoding="utf-8") as handle:
            self.assertTrue(all("arms" not in r for r in map(json.loads, handle)))
        self.assertTrue(all("arms" not in e for e in shadowio.read(self.sb.state)[0]))
        self.assertEqual(armsio.arms_for(self.cfg, ""), {})

    def test_stopped_arm_and_cache_follow_the_directory(self) -> None:
        put_arm(self.sb.state, "demo", salt="s1")
        self.assertEqual(set(armsio.running(self.cfg)), {"demo"})
        put_arm(self.sb.state, "demo", salt="s1", status="stopped")
        os.utime(os.path.join(self.sb.state, "arms"), ns=(1, time.time_ns() + 10**9))
        self.assertEqual(armsio.running(self.cfg), {})


class RunRow(CardCase):
    def test_runs_row_carries_arms_only_when_one_runs(self) -> None:
        self.dirty()
        cardrun.run_locked(self.cfg(), SID, self.job(), self.model(card()))
        self.assertNotIn("arms", cardjob.read_runs(self.cfg())[-1])
        put_arm(self.sb.state, "demo", fraction=0.0)
        self.redirty()
        cardrun.run_locked(self.cfg(), SID, self.job(), self.model(card(key=KEY)))
        self.assertEqual(cardjob.read_runs(self.cfg())[-1]["arms"], {"demo": "off"})


@subproc
class HookImports(unittest.TestCase):
    def test_armsio_stays_light(self) -> None:
        probe = "import sys; sys.path.insert(0, sys.argv[1]); import juno.adapters.armsio; print(sorted(sys.modules))"
        done = subprocess.run(
            [PYTHON, "-I", "-S", "-c", probe, REPO],
            capture_output=True,
            text=True,
            env=clean_env(),
            timeout=30,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        for banned in ("sqlite3", "subprocess", "juno.core.armmetrics", "juno.core.eval"):
            self.assertNotIn(f"'{banned}'", done.stdout)


if __name__ == "__main__":
    unittest.main()
