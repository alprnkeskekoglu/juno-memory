import json
import os
import time

from tests.support import legacy_vault as legacy
from tests.support.write import WriteCase

QUOTE = "this is the first typed prompt"
LINE = "Digest: nothing found"
DAY = time.strftime("%Y-%m-%d")
HEAD = "---\ndate: 2026-11-01\nrepos: [app]\ntickets: []\nsource: 57fb7685\n---\n\n"


class Base(WriteCase):
    def ok(self, *argv: str) -> str:
        code, out = self.juno(*argv)
        self.assertEqual(code, 0, out)
        return out

    def wid(self, out: str) -> str:
        return out.split("(write ")[1].split(";")[0]

    def notes(self) -> list[str]:
        return sorted(n for n in os.listdir(self.vp("notes")) if n.endswith(".md"))

    def forgotten(self) -> str:
        return self.get(f"archive/decayed/forgotten-{DAY[:7]}.md")

    def doctor_rows(self) -> list[tuple[str, str]]:
        _, out = self.juno("doctor", "--json", *self.sb.install_args())
        return [
            (c["status"], c["detail"])
            for c in json.loads(out)["checks"]
            if c["name"] == "note bodies"
        ]


class Forget(Base):
    def make(self, text: str = LINE) -> tuple[str, str, str]:
        out = self.ok("note", "--slug", "digest", text)
        rel = f"notes/{DAY}-digest.md"
        return rel, self.get(rel), self.wid(out)

    def test_forgetting_the_only_line_removes_the_file_in_one_write(self) -> None:
        rel, before, _ = self.make()
        out = self.ok("forget", f"{rel}:8", "--was", "nothing found", "--evidence", QUOTE)
        self.assertEqual(self.notes(), [])
        archived = self.forgotten()
        self.assertIn(LINE, archived)
        self.assertIn("repos: []", archived)
        self.assertIn("source: 57fb7685", archived)
        self.ok("undo", self.wid(out))
        self.assertEqual(self.get(rel), before)
        self.assertNotIn(LINE, self.forgotten())

    def test_a_note_with_another_line_keeps_its_file(self) -> None:
        rel, _, _ = self.make(LINE + "\nSecond line that stays")
        self.ok("forget", f"{rel}:8", "--was", "nothing found", "--evidence", QUOTE)
        self.assertEqual(self.notes(), [f"{DAY}-digest.md"])
        self.assertIn("Second line that stays", self.get(rel))
        self.assertIn(LINE, self.forgotten())

    def test_undo_of_the_creating_write_deletes_the_file(self) -> None:
        _, _, wid = self.make()
        self.ok("undo", wid)
        self.assertEqual(self.notes(), [])


class Cleanup(Base):
    def seed(self) -> None:
        self.put("notes/2026-11-01-empty.md", HEAD)
        self.put("notes/2026-11-02-blank.md", HEAD + "\n  \n")
        self.put("notes/2026-11-03-full.md", HEAD + "A real finding that stays\n")

    def test_doctor_warns_then_migrate_clears_it(self) -> None:
        self.seed()
        warn = [d for s, d in self.doctor_rows() if s == "WARN"]
        self.assertEqual(len(warn), 2, warn)
        self.assertTrue(all("juno migrate --notes" in d for d in warn), warn)
        self.assertIn("notes/2026-11-01-empty.md", warn[0])
        self.assertNotIn("full", " ".join(warn))
        out = self.ok("migrate", "--notes", "--dry-run")
        self.assertIn("would archive", out)
        self.assertEqual(len(self.notes()), 3)
        out = self.ok("migrate", "--notes")
        self.assertIn("archived", out)
        self.assertEqual(self.notes(), ["2026-11-03-full.md"])
        self.assertIn("source: 57fb7685", self.forgotten())
        self.assertIn("notes/2026-11-01-empty.md", self.forgotten())
        self.assertEqual({s for s, _ in self.doctor_rows()}, {"PASS"})
        before = self.forgotten()
        self.assertIn("no body-less", self.ok("migrate", "--notes"))
        self.assertEqual(self.forgotten(), before)

    def test_legacy_vault_notes_warn_then_migrate_clears_them(self) -> None:
        legacy.build(self.sb.vault)
        warn = [d for s, d in self.doctor_rows() if s == "WARN"]
        self.assertEqual(len(warn), len(legacy.BODYLESS), warn)
        self.ok("migrate", "--notes")
        self.assertEqual(self.notes(), ["2026-11-03-full.md"])
        self.assertEqual({s for s, _ in self.doctor_rows()}, {"PASS"})
