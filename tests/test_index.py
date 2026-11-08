import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unicodedata
import unittest
from pathlib import Path
from typing import Any

from juno.adapters import index, indexquery
from juno.core import search as core
from tests.support import PYTHON, REPO, subproc, timing


class VaultCase(unittest.TestCase):
    preset = "none"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="juno-index-test-"))
        self.vault = self.tmp / "vault"
        self.vault.mkdir()
        self.db = self.tmp / "state" / "index.sqlite3"
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, rel: str, text: str) -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def build(self, state: Path | None = None) -> dict[str, Any]:
        return index.build_index(
            str(self.vault),
            str(self.db),
            None if state is None else str(state),
            self.preset,
        )

    def search(self, query: Any, **kw: Any) -> list[dict[str, Any]]:
        kw.setdefault("preset", self.preset)
        return indexquery.search(str(self.db), query, **kw)

    def paths(self, query: Any, **kw: Any) -> list[str]:
        return [r["relpath"] for r in self.search(query, **kw)]


class Incremental(VaultCase):
    def test_only_changed_files_are_reindexed(self) -> None:
        for i in range(5):
            self.write(f"notes/n{i}.md", f"# Note {i}\n\nalpha body {i}\n")
        first = self.build()
        self.assertEqual((first["scanned"], first["reindexed"], first["deleted"]), (5, 5, 0))
        second = self.build()
        self.assertEqual((second["scanned"], second["reindexed"]), (5, 0))
        path = self.write("notes/n2.md", "# Note 2\n\nzebra changed content\n")
        os.utime(path, ns=(1, 1_700_000_000_000_000_000))
        self.assertEqual(self.build()["reindexed"], 1)
        self.assertEqual(self.paths("zebra"), ["notes/n2.md"])
        self.assertEqual(self.paths("alpha", limit=10).count("notes/n2.md"), 0)

    def test_deleted_file_removed(self) -> None:
        self.write("notes/a.md", "# A\n\nuniquewordone\n")
        gone = self.write("notes/b.md", "# B\n\nuniquewordtwo\n")
        self.build()
        gone.unlink()
        self.assertEqual(self.build()["deleted"], 1)
        self.assertEqual(self.paths("uniquewordtwo"), [])
        self.assertEqual(self.paths("uniquewordone"), ["notes/a.md"])

    def test_skips_dot_dirs_symlinks_non_md_and_generated(self) -> None:
        self.write(".obsidian/x.md", "# X\n\nhiddenterm\n")
        self.write("notes/data.txt", "hiddenterm\n")
        self.write("_generated/learned.md", "# L\n\nhiddenterm\n")
        self.write("notes/ok.md", "# Ok\n\nvisibleterm\n")
        outside = self.tmp / "outside.md"
        outside.write_text("# Out\n\nhiddenterm\n")
        os.symlink(outside, self.vault / "notes" / "link.md")
        os.symlink(self.tmp, self.vault / "linkdir")
        self.assertEqual(self.build()["scanned"], 1)
        self.assertEqual(self.paths("hiddenterm"), [])

    def test_corrupt_db_is_rebuilt(self) -> None:
        self.write("notes/a.md", "# A\n\nrebuildable\n")
        self.build()
        self.db.write_bytes(b"not a database" * 100)
        self.assertEqual(self.build()["reindexed"], 1)
        self.assertEqual(self.paths("rebuildable"), ["notes/a.md"])

    def test_bad_frontmatter_does_not_fail_build(self) -> None:
        self.write(
            "notes/bad.md",
            "---\nkey: [unclosed\n  nested:\n    - a\nfoo: |\n  block\n---\n# T\n\nbadfm word\n",
        )
        (self.vault / "notes").mkdir(exist_ok=True)
        (self.vault / "notes" / "raw.md").write_bytes(b"# Raw\n\nrawbytes \xff\xfe here\n")
        self.assertEqual(self.build()["errors"], 0)
        self.assertEqual(self.paths("badfm"), ["notes/bad.md"])
        self.assertEqual(self.paths("rawbytes"), ["notes/raw.md"])

    def test_language_preset_change_reindexes_everything(self) -> None:
        self.write("notes/a.md", "# A\n\nILIK gece\n")
        self.build()
        self.preset = "tr"
        self.assertEqual(self.build()["reindexed"], 1)
        self.assertEqual(self.paths("ılık"), ["notes/a.md"])


class Passages(unittest.TestCase):
    def test_heading_path_and_size(self) -> None:
        text = "# Top\n\nintro\n\n## Sub\n\n" + ("word " * 400) + "\n\n```\n# not a heading\n```\n"
        ps = core.split_passages(text)
        self.assertEqual(ps[0], ("Top", "intro"))
        self.assertTrue(all(h in ("Top", "Top > Sub") for h, _ in ps))
        self.assertTrue(all(len(t) <= 920 for _, t in ps))
        self.assertGreaterEqual(len([p for p in ps if p[0] == "Top > Sub"]), 3)
        self.assertTrue(any("# not a heading" in t for _, t in ps))

    def test_passage_cap_per_file(self) -> None:
        text = "".join(f"## S{i}\n\nbody {i}\n\n" for i in range(300))
        self.assertEqual(len(core.split_passages(text)), core.MAX_PASSAGES_PER_FILE)

    @timing
    def test_redos_inputs_are_fast(self) -> None:
        lines = (
            "# a" + " " * 20000 + "b",
            "# a" + "#" * 20000 + "b",
            "#" * 20000,
            "# a" + " #" * 10000 + "b",
        )
        for line in lines:
            started = time.perf_counter()
            core.split_passages(line + "\n\nbody text\n")
            self.assertLess((time.perf_counter() - started) * 1000, 50)
            self.assertIsNone(core.heading(line))

    def test_heading_parsing(self) -> None:
        self.assertEqual(core.heading("## Title ##  "), (2, "Title"))
        self.assertEqual(core.heading("#\tTab"), (1, "Tab"))
        self.assertIsNone(core.heading("#nospace"))
        self.assertIsNone(core.heading("####### seven"))
        self.assertIsNone(core.heading("# " + "x" * 500))
        self.assertEqual(core.heading("# " + "x" * 498), (1, "x" * 498))


class Retirement(VaultCase):
    def test_supersedes_by_path_and_wikilink(self) -> None:
        self.write("knowledge/ABC-1.md", "# Old one\n\nsharedterm\n")
        self.write("notes/old-note.md", "# Old note\n\nsharedterm\n")
        self.write(
            "notes/new.md",
            '---\nsupersedes:\n  - knowledge/ABC-1.md\n  - "[[old-note]]"\n---\n# New\n\nsharedterm\n',
        )
        self.build()
        self.assertEqual(self.paths("sharedterm"), ["notes/new.md"])
        self.assertEqual(
            set(index.retired_paths(str(self.db))), {"knowledge/ABC-1.md", "notes/old-note.md"}
        )
        self.assertEqual(index.read_resolution(str(self.db)).dead, [])

    def test_archived_status_excluded(self) -> None:
        self.write("notes/a.md", "---\nstatus: Archived\n---\n# A\n\narchterm\n")
        self.write("notes/b.md", "# B\n\narchterm\n")
        self.build()
        self.assertEqual(self.paths("archterm"), ["notes/b.md"])

    def test_protected_dirs_never_retired(self) -> None:
        self.write("rules/global.md", "# Global\n\nprotterm one\n")
        self.write("tasks/ABC-1.md", "# T\n\nprotterm two\n")
        self.write("decisions/x.md", "# D\n\nprotterm three\n")
        self.write(
            "notes/evil.md",
            '---\nsupersedes:\n  - rules/global\n  - "[[ABC-1]]"\n  - decisions/x.md\n---\n# E\n\nprotterm\n',
        )
        self.build()
        self.assertEqual(index.retired_paths(str(self.db)), {})
        self.assertEqual(len(self.paths("protterm", limit=10)), 4)
        dead = index.read_resolution(str(self.db)).dead
        self.assertEqual({d["reason"] for d in dead}, {"protected"})

    def test_removing_superseder_revives_target(self) -> None:
        self.write("notes/a.md", "# A\n\nreviveterm\n")
        newer = self.write("notes/b.md", "---\nsupersedes: notes/a.md\n---\n# B\n\nother\n")
        self.build()
        self.assertEqual(self.paths("reviveterm"), [])
        newer.unlink()
        self.build()
        self.assertEqual(self.paths("reviveterm"), ["notes/a.md"])

    def test_retired_json_written_with_state_dir(self) -> None:
        self.write("notes/arch.md", "---\nstatus: archived\n---\n# Arch\n\nx\n")
        self.write("notes/b-old.md", "# Old\n\nx\n")
        self.write("notes/a-new.md", "---\nsupersedes: notes/b-old.md\n---\n# New\n\nx\n")
        state = self.tmp / "state2"
        self.build(state)
        import json

        doc = json.loads((state / "retired.json").read_text())
        self.assertEqual(
            doc["retired"],
            [
                {"path": "notes/arch.md", "by": None, "reason": "archived"},
                {"path": "notes/b-old.md", "by": "notes/a-new.md", "reason": "supersedes"},
            ],
        )
        self.assertEqual((state / "retired.json").stat().st_mode & 0o777, 0o600)
        stamp = (state / "retired.json").stat().st_mtime_ns
        self.build(state)
        self.assertEqual((state / "retired.json").stat().st_mtime_ns, stamp)

    def test_nfc_nfd_resolution(self) -> None:
        nfd = unicodedata.normalize("NFD", "notes/café")
        nfc = unicodedata.normalize("NFC", "notes/café")
        self.write(nfd + ".md", "nfdterm\n")
        self.write("notes/r.md", f"---\nsupersedes: {nfc}\n---\nz\n")
        self.build()
        self.assertEqual(self.paths("nfdterm"), [])
        self.assertEqual(index.read_resolution(str(self.db)).dead, [])


class Archive(VaultCase):
    def test_archive_is_indexed_but_flagged_and_off_by_default(self) -> None:
        self.write("archive/handoffs/2026-01.md", "# Old\n\narchword\n")
        self.write("notes/ARCHIVE/deep/c.md", "archword in nested archive\n")
        self.write("notes/keep.md", "archword and keepword\n")
        self.assertEqual(self.build()["scanned"], 3)
        self.assertEqual(self.paths("archword", limit=10), ["notes/keep.md"])
        hits = self.search("archword", limit=10, archive=True)
        flagged = {h["relpath"]: h.get("archive", False) for h in hits}
        self.assertEqual(
            flagged,
            {
                "archive/handoffs/2026-01.md": True,
                "notes/ARCHIVE/deep/c.md": True,
                "notes/keep.md": False,
            },
        )

    def test_archive_files_do_not_retire_anything(self) -> None:
        self.write("notes/a.md", "# A\n\nlivefile\n")
        self.write("archive/x.md", "---\nsupersedes: notes/a.md\n---\n# X\n\nlivefile\n")
        self.build()
        self.assertEqual(self.paths("livefile", archive=False), ["notes/a.md"])


class Privacy(VaultCase):
    def test_private_files_indexed_but_never_returned(self) -> None:
        self.write("notes/p.md", "---\nvisibility: Private\n---\n# P\n\nprivterm\n")
        self.write("notes/q.md", "---\nvisibility: public\n---\n# Q\n\nprivterm\n")
        self.assertEqual(self.build()["reindexed"], 2)
        self.assertEqual(self.paths("privterm", limit=10, archive=True), ["notes/q.md"])
        self.assertEqual(self.paths("P", limit=10), [])

    def test_candidates_in_private_files_are_not_offered_as_corrections(self) -> None:
        self.write("notes/p.md", "---\nvisibility: private\n---\n# P\n\nsecretword\n")
        self.build()
        found: dict[str, list[str]] = {}
        self.assertEqual(self.search("secretwrod", corrections=found), [])
        self.assertEqual(found, {})


class DbSafety(VaultCase):
    def test_victim_file_is_not_destroyed(self) -> None:
        victim = self.write("core.md", "# Core\n\nimportant\n")
        with self.assertRaises(ValueError):
            index.build_index(str(self.vault), str(victim))
        with self.assertRaises(ValueError):
            index.build_index(str(self.vault), str(self.vault / "sub" / "i.db"))
        self.assertEqual(victim.read_text(), "# Core\n\nimportant\n")

    def test_garbage_db_is_moved_aside_not_unlinked(self) -> None:
        self.write("notes/a.md", "# A\n\ngarbageterm\n")
        self.build()
        self.db.write_bytes(b"not a database" * 100)
        self.build()
        self.assertEqual(self.paths("garbageterm"), ["notes/a.md"])
        self.assertTrue(Path(str(self.db) + ".bad").exists())

    def test_locked_db_raises_and_survives(self) -> None:
        self.write("notes/a.md", "# A\n\nlockterm\n")
        self.build()
        self.write("notes/b.md", "# B\n\nlockterm2\n")
        blocker = sqlite3.connect(self.db, isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")
        real_connect = sqlite3.connect
        try:
            sqlite3.connect = lambda *a, **k: real_connect(*a, **{**k, "timeout": 0.1})  # type: ignore[assignment]
            with self.assertRaises(sqlite3.OperationalError):
                self.build()
        finally:
            sqlite3.connect = real_connect
            blocker.rollback()
            blocker.close()
        self.assertEqual(self.paths("lockterm"), ["notes/a.md"])
        self.assertTrue(self.db.exists())

    def test_schema_mismatch_rebuilds_but_foreign_db_refused(self) -> None:
        self.write("notes/a.md", "# A\n\nschematerm\n")
        self.build()
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA user_version=7")
        conn.close()
        self.assertEqual(self.build()["reindexed"], 1)
        self.assertEqual(self.paths("schematerm"), ["notes/a.md"])
        foreign = self.tmp / "state" / "foreign.db"
        conn = sqlite3.connect(foreign)
        conn.execute("CREATE TABLE precious (x)")
        conn.commit()
        conn.close()
        with self.assertRaises(RuntimeError):
            index.build_index(str(self.vault), str(foreign))
        conn = sqlite3.connect(foreign)
        self.assertEqual(conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0], 1)
        conn.close()

    def test_permissions_and_umask_restored(self) -> None:
        old = os.umask(0o022)
        try:
            self.write("notes/a.md", "# A\n\npermterm\n")
            self.build()
            self.assertEqual(os.umask(0o022), 0o022)
        finally:
            os.umask(old)
        self.assertEqual(self.db.parent.stat().st_mode & 0o777, 0o700)
        for item in self.db.parent.iterdir():
            self.assertEqual(item.stat().st_mode & 0o077, 0, item.name)

    def test_missing_index_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            indexquery.search(str(self.tmp / "nope.sqlite3"), "x")

    def test_swapped_inode_and_symlink_are_not_read(self) -> None:
        path = self.write("notes/a.md", "# A\n\nswapterm\n")
        st = path.stat()
        self.assertIsNone(index.read_file(str(path), st.st_dev, st.st_ino + 1))
        self.assertIsNotNone(index.read_file(str(path), st.st_dev, st.st_ino))
        link = self.vault / "notes" / "l.md"
        os.symlink(self.tmp / "nowhere", link)
        with self.assertRaises(OSError):
            index.read_file(str(link), st.st_dev, st.st_ino)

    def test_unreadable_file_drops_stale_passages(self) -> None:
        path = self.write("notes/gone.md", "stalesecret\n")
        self.build()
        self.assertEqual(self.paths("stalesecret"), ["notes/gone.md"])
        path.write_text("stalesecret and more changed\n")
        os.chmod(path, 0)
        try:
            if os.access(path, os.R_OK):
                self.skipTest("running with permission bypass")
            self.assertEqual(self.build()["errors"], 1)
            self.assertEqual(self.paths("stalesecret"), [])
        finally:
            os.chmod(path, 0o644)


@subproc
class ParallelBuilds(VaultCase):
    def test_parallel_first_builds(self) -> None:
        for i in range(60):
            self.write(f"notes/n{i}.md", f"alpha {i}\n" * 30)
        code = (
            f"import sys;sys.path.insert(0,{REPO!r});from juno.adapters import index;"
            f"print(index.build_index({str(self.vault)!r},{str(self.db)!r})['scanned'])"
        )
        procs = [
            subprocess.Popen(
                [PYTHON, "-I", "-S", "-c", code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(4)
        ]
        for proc in procs:
            out, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
            self.assertEqual(out.strip(), "60")
        self.assertEqual(len(self.search("alpha", limit=100)), 60)
        self.assertEqual(self.build()["reindexed"], 0)


if __name__ == "__main__":
    unittest.main()
