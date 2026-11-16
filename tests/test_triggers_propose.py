"""#36 follow-up: `juno triggers propose` queues a trigger candidate per untriggered file."""

import hashlib
import os
import re
import unittest
from unittest import mock

from juno.adapters import queues
from juno.core import candidates
from tests.support.write import WriteCase

FILES = {
    "knowledge/alpha.md": "---\ntitle: Alpha pipeline\nsources: [x]\n---\nThe pipeline retries.\n",
    "knowledge/beta.md": "---\ntitle: Beta gateway\nsources: [x]\n---\nThe gateway times out.\n",
    "knowledge/gamma.md": "---\ntitle: Gamma scheduler\nsources: [x]\n---\nScheduler drift.\n",
    "knowledge/done.md": "---\ntitle: Done\nsources: [x]\ntriggers: done-thing\n---\nbody\n",
}


def digest(root: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for base, _, names in os.walk(root):
        for name in names:
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                found[os.path.relpath(path, root)] = hashlib.sha256(handle.read()).hexdigest()
    return found


class Propose(WriteCase):
    def setUp(self) -> None:
        super().setUp()
        for rel, text in FILES.items():
            self.put(rel, text)

    def triggers_items(self) -> list[queues.Item]:
        return [i for i in queues.read(self.cfg, queues.RULES) if candidates.is_triggers(i)]

    def test_criterion_10_one_candidate_per_untriggered_file_and_no_file_changed(self) -> None:
        before = digest(self.sb.vault)
        code, out = self.juno("triggers", "propose")
        self.assertEqual(code, 0, out)
        self.assertIn("3 trigger candidates queued", out)
        items = self.triggers_items()
        self.assertEqual(
            sorted(i["file"] for i in items),
            ["knowledge/alpha.md", "knowledge/beta.md", "knowledge/gamma.md"],
        )
        self.assertTrue(all(i["status"] == "pending" and i["triggers"] for i in items))
        self.assertEqual(digest(self.sb.vault), before)

    def test_criterion_11_accepting_one_changes_only_that_file(self) -> None:
        self.juno("triggers", "propose")
        items = {i["file"]: i for i in self.triggers_items()}
        alpha = items["knowledge/alpha.md"]
        before = digest(self.sb.vault)
        self.prompt(f"yes, accept {alpha['id']}", "pid00002")
        code, out = self.juno("rule", "accept", alpha["id"])
        self.assertEqual(code, 0, out)
        after = digest(self.sb.vault)
        changed = {k for k in after if k.startswith("knowledge/") and before.get(k) != after[k]}
        self.assertEqual(changed, {"knowledge/alpha.md"})
        self.assertRegex(self.get("knowledge/alpha.md"), r"(?m)^triggers: ")
        self.assertNotIn("triggers:", self.get("knowledge/beta.md"))
        status = {i["file"]: i["status"] for i in self.triggers_items()}
        self.assertEqual(status["knowledge/alpha.md"], "accepted")
        self.assertEqual(status["knowledge/beta.md"], "pending")
        self.assertEqual(status["knowledge/gamma.md"], "pending")

    def test_criterion_12_a_second_run_queues_nothing_new(self) -> None:
        self.juno("triggers", "propose")
        first = self.triggers_items()
        code, out = self.juno("triggers", "propose")
        self.assertEqual(code, 0, out)
        self.assertIn("0 trigger candidates queued", out)
        self.assertIn("3 already queued", out)
        self.assertEqual(self.triggers_items(), first)

    def test_a_decided_candidate_is_not_proposed_again(self) -> None:
        self.juno("triggers", "propose")
        beta = next(i for i in self.triggers_items() if i["file"] == "knowledge/beta.md")
        self.prompt(f"reject {beta['id']}", "pid00002")
        self.juno("rule", "reject", beta["id"])
        self.juno("triggers", "propose")
        self.assertEqual(len(self.triggers_items()), 3)

    def test_ordinary_slots_are_not_used_and_the_batch_has_its_own_limit(self) -> None:
        self.juno("triggers", "propose")
        queue = queues.read(self.cfg, queues.RULES)
        self.assertTrue(all(i["batch"] == candidates.PROPOSE_BATCH for i in queue))
        self.assertEqual(candidates.ordinary_pending(queue), [])
        self.assertEqual(candidates.rules_nudge(queue), "")
        self.put(
            "knowledge/delta.md", "---\ntitle: Delta ledger\nsources: [x]\n---\nLedger rows.\n"
        )
        with mock.patch.object(candidates, "PROPOSE_MAX", 3):
            code, out = self.juno("triggers", "propose")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.triggers_items()), 3)
        self.assertIn("1 over the limit of 3", out)

    def test_pending_proposals_do_not_expire(self) -> None:
        self.juno("triggers", "propose")
        queue = queues.read(self.cfg, queues.RULES)
        later = candidates.number(queue[0]["t"]) + 400 * candidates.DAY
        kept = candidates.prune(queue, later)
        self.assertEqual(len(kept), 3)
        self.assertTrue(all(i["status"] == "pending" for i in kept))

    def test_refused_in_agent_context(self) -> None:
        code, out = self.juno("triggers", "propose", env={"CLAUDE_CODE_AGENT_ID": "sub-1"})
        self.assertNotEqual(code, 0)
        self.assertRegex(out, re.compile("agent context|not an interactive", re.I))
        self.assertEqual(self.triggers_items(), [])


if __name__ == "__main__":
    unittest.main()
