import json
import os
import unittest

from juno.core import migrate, privacyack
from tests.support import decision_vault as dv
from tests.support.write import WriteCase
from tests.test_migrate_decisions import snapshot

ACK = ".privacy-ack.jsonl"
FAKE = "000000000000"  # a flagged id shape, made up
ID_LINE = dv.line(f"Pull from the mirror at {FAKE} for the nightly job")
OLD = "decisions/ABC-1.md"
NEW = "tasks/ABC-1.md"


def lineno(text: str, needle: str) -> int:
    return next(n for n, x in enumerate(text.splitlines(), 1) if needle in x)


class Acks(WriteCase):
    TICKETS = True

    def setUp(self) -> None:
        super().setUp()
        dv.build(self.sb.vault)
        text = self.get(OLD)
        self.put(OLD, text.replace("\n\n## History", f"\n{ID_LINE}\n\n## History", 1))
        self.put("notes/other.md", f"# Other\n\n- see {FAKE} here\n")
        self.write_acks(
            [self.rec(OLD, FAKE, lineno(self.get(OLD), FAKE)), self.rec("notes/other.md", FAKE, 3)]
        )

    def rec(self, rel: str, span: str, line: int) -> dict[str, object]:
        return privacyack.record(rel, span, line, "made-up evidence", "2026-11-01")

    def write_acks(self, records: list[dict[str, object]]) -> None:
        rows = [json.dumps(r, sort_keys=True) for r in records]
        self.put(ACK, "\n".join(rows) + "\n")

    def acks(self) -> list[dict[str, object]]:
        return privacyack.parse(self.get(ACK))

    def move(self, *extra: str) -> str:
        code, out = self.juno("migrate", "--decisions", *extra)
        self.assertEqual(code, 0, out)
        return out

    def privacy_warns(self) -> list[str]:
        _, out = self.juno("doctor", "--json", *self.sb.install_args())
        return [
            c["detail"]
            for c in json.loads(out)["checks"]
            if c["name"] == "privacy" and c["status"] == "WARN"
        ]

    def listing(self) -> str:
        code, out = self.juno("privacy", "list")
        self.assertEqual(code, 0, out)
        return out

    def orphan(self) -> None:
        """The state of a vault the 1.0.11 move already ran on."""
        self.move()
        self.write_acks([self.rec(OLD, FAKE, 8), self.rec("notes/other.md", FAKE, 3)])

    def test_move_carries_the_ack_with_its_line(self) -> None:
        out = self.move()
        self.assertIn("decisions: 1 privacy acknowledgements moved with their lines", out)
        number = lineno(self.get(NEW), FAKE)
        mine = [r for r in self.acks() if r["file"] != "notes/other.md"]
        self.assertEqual([(r["file"], r["line"]) for r in mine], [(NEW, number)])
        self.assertEqual(self.privacy_warns(), [])
        self.assertIn(f"- {NEW}:{number} ", self.listing())
        self.assertNotIn(OLD, self.listing())

    def test_orphaned_ack_of_a_migrated_vault_is_repaired_by_the_rerun(self) -> None:
        self.orphan()
        self.assertTrue(self.privacy_warns())
        out = self.move()
        self.assertIn("decisions: 1 privacy acknowledgements moved with their lines", out)
        self.assertEqual(self.privacy_warns(), [])
        self.assertIn(f"- {NEW}:{lineno(self.get(NEW), FAKE)} ", self.listing())

    def test_orphan_is_repaired_by_the_schema_step(self) -> None:
        self.orphan()
        toml = self.vp("juno-brain.toml")
        self.put("juno-brain.toml", migrate.write_schema(self.get("juno-brain.toml"), 5))
        self.assertTrue(os.path.exists(toml))
        self.assertEqual(self.juno("migrate")[0], 0)
        self.assertEqual(self.privacy_warns(), [])

    def test_dry_run_reports_and_writes_nothing(self) -> None:
        before = snapshot(self.sb.vault)
        out = self.move("--dry-run")
        self.assertIn("decisions: 1 privacy acknowledgements would move with their lines", out)
        self.assertEqual(snapshot(self.sb.vault), before)
        self.orphan()
        before = snapshot(self.sb.vault)
        out = self.move("--dry-run")
        self.assertIn("decisions: 1 privacy acknowledgements would move with their lines", out)
        self.assertEqual(snapshot(self.sb.vault), before)

    def test_ack_whose_span_is_not_in_the_owner_stays(self) -> None:
        hist = dv.line(f"Old mirror {FAKE} dropped", "2026-09-01")
        text = self.get(OLD).replace("\n\n## History", f"\n\n## History\n{hist}", 1)
        text = text.replace(ID_LINE + "\n", "")
        self.put(OLD, text)
        mine = self.rec(OLD, FAKE, lineno(text, FAKE))
        self.write_acks([mine, self.rec("notes/other.md", FAKE, 3)])
        out = self.move()
        self.assertIn(FAKE, self.get("archive/decisions/ABC-1.md"))
        self.assertIn("decisions: 1 privacy acknowledgements stay", out)
        self.assertNotIn("moved with their lines", out)
        self.assertIn(OLD, [r["file"] for r in self.acks()])
        self.assertEqual(self.acks()[0], mine)

    def test_second_run_changes_nothing(self) -> None:
        self.move()
        after = snapshot(self.sb.vault)
        out = self.move()
        self.assertNotIn("moved with their lines", out)
        self.assertEqual(snapshot(self.sb.vault), after)

    def test_ack_on_an_unrelated_file_is_untouched(self) -> None:
        self.move()
        other = [r for r in self.acks() if r["file"] == "notes/other.md"]
        self.assertEqual(len(other), 1)
        self.assertEqual(other[0]["line"], 3)

    def test_ack_file_is_backed_up(self) -> None:
        old = self.get(ACK)
        self.move()
        found = []
        for base, _, names in os.walk(self.sb.state):
            for name in names:
                if base.split(os.sep)[-2:-1] == ["backups"] and name.endswith(
                    ACK.replace("/", "%")
                ):
                    with open(os.path.join(base, name), encoding="utf-8") as handle:
                        found.append(handle.read())
        self.assertIn(old, found)


if __name__ == "__main__":
    unittest.main()
