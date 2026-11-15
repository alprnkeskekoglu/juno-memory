import json
import os
import time

from juno.adapters import writelog
from tests.support import decision_vault as dv
from tests.support.write import WriteCase

QUOTE = "this is the first typed prompt"
DAY = time.strftime("%Y-%m-%d")
TRACKED = ("projects/alpha.md", "archive/decisions/alpha.md")


class Base(WriteCase):
    TICKETS = True

    def ok(self, *argv: str) -> str:
        code, out = self.juno(*argv)
        self.assertEqual(code, 0, out)
        return out

    def snapshot(self, files: tuple[str, ...]) -> dict[str, str]:
        return {f: self.get(f) for f in files}

    def linked(self, verb: str) -> tuple[str, str]:
        rows = writelog.read(self.cfg)
        (child,) = [r for r in rows if r.get("verb") == verb]
        return str(child["id"]), str(child["link"])

    def refused_child(self, child: str, parent: str, files: tuple[str, ...]) -> None:
        before = self.snapshot(files)
        count = len(writelog.read(self.cfg))
        code, out = self.juno("undo", child)
        self.assertEqual(code, 2, out)
        self.assertIn(f"write {child} is part of {parent}; undo {parent}", out)
        self.assertEqual(self.snapshot(files), before)
        self.assertEqual(len(writelog.read(self.cfg)), count)


class Supersede(Base):
    def test_the_archive_half_is_refused_and_the_parent_restores_both_files(self) -> None:
        dv.build(self.sb.vault)
        self.ok("migrate", "--decisions")
        before = self.snapshot(TRACKED)
        self.ok(
            "decision", "--topic", "alpha", "--evidence", QUOTE, "Pin images by digest and tag",
            "--supersedes", "1",
        )  # fmt: skip
        after = self.snapshot(TRACKED)
        self.assertNotEqual(after, before)
        child, parent = self.linked("decision-archive")
        self.refused_child(child, parent, TRACKED)
        self.assertEqual(self.snapshot(TRACKED), after)
        self.ok("undo", parent)
        self.assertEqual(self.snapshot(TRACKED), before)


class Forget(Base):
    def test_the_forget_archive_half_is_refused_and_the_parent_restores(self) -> None:
        self.ok("note", "--slug", "digest", "Digest: nothing found\nSecond line stays")
        rel = f"notes/{DAY}-digest.md"
        files = (rel, f"archive/decayed/forgotten-{DAY[:7]}.md")
        before = self.snapshot(files)
        self.ok("forget", f"{rel}:8", "--was", "nothing found", "--evidence", QUOTE)
        child, parent = self.linked("forget-archive")
        self.refused_child(child, parent, files)
        self.ok("undo", parent)
        self.assertEqual(self.snapshot(files), before)


class Run(Base):
    def test_a_run_reverts_a_linked_pair_once(self) -> None:
        self.ok("note", "--slug", "digest", "Digest: nothing found\nSecond line stays")
        rel = f"notes/{DAY}-digest.md"
        files = (rel, f"archive/decayed/forgotten-{DAY[:7]}.md")
        before = self.snapshot(files)
        self.ok("forget", f"{rel}:8", "--was", "nothing found", "--evidence", QUOTE)
        child, parent = self.linked("forget-archive")
        rows = writelog.read(self.cfg)
        run_id = "run-test"
        path = os.path.join(self.sb.state, "writes.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for r in rows:
                if r["id"] in (child, parent):
                    r = {**r, "run": run_id}
                handle.write(json.dumps(r) + "\n")
        code, out = self.juno("undo", "--run", run_id)
        self.assertEqual(code, 0, out)
        self.assertIn("1 of 1 writes reverted", out)
        self.assertEqual(self.snapshot(files), before)
        code, out = self.juno("undo", "--run", run_id)
        self.assertEqual(code, 2, out)
