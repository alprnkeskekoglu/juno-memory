import dataclasses
import json
import os
import unittest
from typing import Any

from juno.adapters.vaultview import DiskVault
from juno.core import packs, stale
from juno.core.ledger import Segment
from juno.handlers import delivery
from tests.support import decision_vault as dv
from tests.support.write import WriteCase

QUOTE = "this is the first typed prompt"
GONE = "- Keep the exporter in `src/gone_exporter.py` (user pid00001, 2026-11-01)"


class Base(WriteCase):
    TICKETS = True

    def ok(self, *argv: str) -> str:
        code, out = self.juno(*argv)
        self.assertEqual(code, 0, out)
        return out

    def pack(self, kind: str, key: str) -> str:
        return delivery.deliver_packs(
            self.cfg, DiskVault(self.cfg), Segment("sid-x"), [(kind, key)], 10**6
        )


class Scenario(Base):
    """One vault, start to end: doctor, migrate, search, a new ticket decision, supersede."""

    def setUp(self) -> None:
        super().setUp()
        dv.build(self.sb.vault)

    def decision_checks(self) -> list[str]:
        out = self.ok("doctor", "--json", *self.sb.install_args())
        found = [c for c in json.loads(out)["checks"] if c["name"] == "decisions"]
        return [f"{c['status']} {c['detail']}" for c in found]

    def search(self, *argv: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = json.loads(self.ok("search", *argv, "--json"))["results"]
        return results

    def test_the_whole_move_on_one_vault(self) -> None:
        warns = [m for m in self.decision_checks() if m.startswith("WARN")]
        self.assertTrue(any("belongs in tasks/ABC-1.md" in m for m in warns))
        self.assertTrue(any("juno migrate --decisions" in m for m in warns))
        self.ok("migrate", "--decisions")
        self.assertFalse([m for m in self.decision_checks() if m.startswith("WARN")])
        for key in dv.TICKETS:
            self.assertFalse(os.path.exists(self.vp(f"decisions/{key}.md")), key)
        for topic in dv.OWNERLESS:
            self.assertTrue(os.path.exists(self.vp(f"decisions/{topic}.md")), topic)

        # A6 search: the moved line is found in the owner file, not a decisions/ file
        paths = [r["relpath"] for r in self.search("second choice for ABC-25")]
        self.assertIn("tasks/ABC-25.md", paths)
        self.assertFalse([p for p in paths if p.startswith("decisions/ABC")])

        # A5: a ticket with no task file and no decision file gets the stub in the same write
        self.assertFalse(os.path.exists(self.vp("tasks/ABC-99.md")))
        self.ok(
            "decision", "--topic", "ABC-99", "--evidence", QUOTE, "Ship the export behind a flag"
        )
        stub = self.get("tasks/ABC-99.md")
        self.assertIn("jira: ABC-99\n", stub)
        self.assertIn("created: ", stub)
        self.assertIn("updated: ", stub)
        self.assertNotIn("title:", stub)
        self.assertFalse(os.path.exists(self.vp("decisions/ABC-99.md")))
        pack = self.pack("ticket", "ABC-99")
        self.assertIn("[Decisions · tasks/ABC-99.md]\n- Ship the export behind a flag (user", pack)

        # A3: --supersedes moves the old line to the archive; pack and default search drop it
        old = dv.ALPHA_DUPLICATE
        self.assertIn(old, self.pack("repo", "alpha"))
        out = self.ok(
            "decision", "--topic", "alpha", "--evidence", QUOTE, "Pin images by digest and tag",
            "--supersedes", "1",
        )  # fmt: skip
        self.assertIn("projects/alpha.md", out)
        self.assertNotIn(old, self.pack("repo", "alpha"))
        self.assertIn("Pin images by digest and tag", self.pack("repo", "alpha"))
        self.assertIn(old, self.get("archive/decisions/alpha.md"))
        query = "Pin the base image to a digest"
        self.assertFalse([r for r in self.search(query) if "base image" in r["snippet"]])
        hits = [r for r in self.search(query, "--archive") if "base image" in r["snippet"]]
        self.assertEqual(
            [(r["relpath"], r.get("superseded")) for r in hits],
            [("archive/decisions/alpha.md", True)],
        )

        # A6 stale: a path cited only under ## Decisions is outside the scan text
        self.ok("decision", "--topic", "alpha", "--evidence", QUOTE, GONE.split(" (user")[0][2:])
        text = self.get("projects/alpha.md")
        self.assertIn("gone_exporter.py", text)
        self.assertNotIn("gone_exporter.py", stale.without_decisions(text))
        self.assertIn("upload job needs the VPN", stale.without_decisions(text))


class LoggedPack(Base):
    def test_real_task_log_sections_sit_after_decisions_and_survive_a_tight_budget(self) -> None:
        self.put("tasks/ABC-70.md", "---\njira: ABC-70\ntitle: T\n---\n\n## Notes\n- a note\n")
        decided = ["Use queues for retries", "Ship behind a flag", "Cache results for an hour"]
        for text in decided:
            self.ok("decision", "--topic", "ABC-70", "--evidence", QUOTE, text)
        for n in range(4):
            self.ok(
                "task", "log", "ABC-70", "--section", "Plan", f"--key=s{n}", "--evidence", QUOTE,
                f"plan step {n} " + "with padding words " * 8,
            )  # fmt: skip
        text = self.pack("ticket", "ABC-70")
        order = [
            "[Decisions · tasks/ABC-70.md]",
            "[Plan · tasks/ABC-70.md]",
            "[Notes · tasks/ABC-70.md]",
        ]
        positions = [text.index(item) for item in order]
        self.assertEqual(positions, sorted(positions))
        view, cfg = DiskVault(self.cfg), self.cfg
        small = dataclasses.replace(packs.Caps.from_config(cfg), pack_chars=650)
        tight = packs.render_pack(view, small, "ticket", "ABC-70", [])
        self.assertIn("Juno truncated", tight)
        for item in decided:
            self.assertIn(item, tight)


if __name__ == "__main__":
    unittest.main()
