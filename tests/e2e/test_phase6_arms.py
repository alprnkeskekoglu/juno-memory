import json
import os
import time
from typing import Any

from tests.e2e import harness
from tests.e2e.harness import JUNO_BIN
from tests.e2e.test_phase2_cli import Case, command
from tests.support import e2e


def prereg(fraction: float) -> str:
    return json.dumps(
        {
            "hypothesis": "demo arm",
            "assignment": {"fraction": fraction},
            "unit": "a segment",
            "metric": {"primary": "segments per arm", "code": "segments"},
            "min_n": 5,
            "max_days": 30,
            "stop_rule": "decide at min_n or max_days",
            "decision": "on if on beats off",
        }
    )


@e2e
class ArmAssignmentLogged(Case):
    def prereg_file(self, name: str, fraction: float) -> str:
        os.makedirs(self.sb.state, exist_ok=True)
        path = os.path.join(self.sb.state, f"{name}-prereg.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(prereg(fraction))
        return path

    def records(self, name: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        directory = os.path.join(self.sb.state, name)
        for file in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
            with open(os.path.join(directory, file), encoding="utf-8") as handle:
                found += [json.loads(line) for line in handle if line.strip()]
        return found

    def test_arm_is_assigned_by_salted_hash_and_logged_and_a_third_is_refused(self) -> None:
        self.assertEqual(harness.shim(self.sb, "search", "demo").returncode, 0)
        files = {
            n: self.prereg_file(n, f) for n, f in (("demo", 1.0), ("second", 0.0), ("third", 1.0))
        }
        steps = [
            f"Step {i}: " + command(JUNO_BIN, "arm", "start", name, "--prereg", files[name])
            for i, name in enumerate(("demo", "second", "third"), 1)
        ]
        run = self.go(" ".join(steps) + " Then reply with the word done.")
        out = self.output(run)
        self.assertEqual(out.count("Juno: refused:"), 1, run.raw[-3000:])
        arm_dir = os.path.join(self.sb.state, "arms")
        self.assertEqual(
            sorted(os.listdir(arm_dir)), ["arms.lock", "demo.json", "log.jsonl", "second.json"]
        )

        self.go("Reply with the single word ok. Codeword demo")
        starts = [r for r in self.records("ledgers") if r.get("ev") == "start" and r.get("arms")]
        self.assertEqual(len(starts), 1, starts)
        self.assertEqual(starts[0]["arms"], {"demo": "on", "second": "off"})
        entries = [e for e in self.records_shadow() if e.get("arms")]
        self.assertEqual([e["arms"] for e in entries], [{"demo": "on", "second": "off"}])
        self.assertLess(abs(time.time() - entries[0]["ts"]), 3600)

    def records_shadow(self) -> list[dict[str, Any]]:
        path = os.path.join(self.sb.state, "shadow.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
