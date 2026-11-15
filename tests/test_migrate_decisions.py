import json
import os
import unittest

from juno.adapters import store
from juno.adapters.vaultview import DiskVault
from juno.core import migrate, vaultfmt
from tests.support import decision_vault as dv
from tests.support.write import WriteCase


def snapshot(root: str) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for base, _, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                found[os.path.relpath(path, root)] = handle.read()
    return found


class Moves(WriteCase):
    TICKETS = True

    def setUp(self) -> None:
        super().setUp()
        dv.build(self.sb.vault)

    def exists(self, rel: str) -> bool:
        return os.path.exists(self.vp(rel))

    def mtime(self, rel: str) -> int:
        return int(os.path.getmtime(self.vp(rel)))

    def lines(self, rel: str) -> list[str]:
        return vaultfmt.section(self.get(rel), "Decisions").splitlines()

    def move(self, *extra: str) -> str:
        code, out = self.juno("migrate", "--decisions", *extra)
        self.assertEqual(code, 0, out)
        return out

    def test_owned_files_leave_decisions_and_ownerless_stay(self) -> None:
        out = self.move()
        for key in dv.TICKETS:
            self.assertFalse(self.exists(f"decisions/{key}.md"), key)
            self.assertTrue(self.exists(f"tasks/{key}.md"), key)
            self.assertIn(f"moved decisions/{key}.md to tasks/{key}.md (", out)
        for topic in dv.OWNERLESS:
            self.assertTrue(self.exists(f"decisions/{topic}.md"), topic)
        for name in ("alpha", "beta"):
            self.assertFalse(self.exists(f"decisions/{name}.md"))

    def test_owner_sections_hold_the_merged_lines_in_order(self) -> None:
        self.move()
        self.assertEqual(
            self.lines("tasks/ABC-3.md"),
            [
                dv.line("a choice already in the task file"),
                dv.line("first choice for ABC-3"),
                dv.line("second choice for ABC-3"),
            ],
        )
        self.assertEqual(
            self.lines("projects/alpha.md"),
            [
                dv.ALPHA_DUPLICATE,
                "- Only in the profile (user pid00001, 2026-10-04)",
                dv.ALPHA_NEAR_A,
                dv.ALPHA_NEAR_B,
                dv.line("Only in the legacy file"),
            ],
        )
        self.assertEqual(self.lines("projects/beta.md"), [dv.line("Use one queue")])
        self.assertEqual(self.lines("tasks/ABC-1.md")[0], dv.line("first choice for ABC-1"))
        self.assertIn("## Notes\n- note about ABC-1", self.get("tasks/ABC-1.md"))
        self.assertIn("## Gotchas\n- the upload job needs the VPN", self.get("projects/alpha.md"))

    def test_history_goes_to_the_archive_unchanged(self) -> None:
        out = self.move()
        archive = self.get("archive/decisions/alpha.md")
        self.assertIn("## History", archive)
        self.assertIn(dv.line("Used a floating base image", "2026-09-01"), archive.splitlines())
        self.assertIn(
            dv.line("old choice for ABC-2", "2026-09-01"), self.get("archive/decisions/ABC-2.md")
        )
        self.assertFalse(self.exists("archive/decisions/ABC-5.md"))
        self.assertIn("(2 lines, 1 to archive/decisions/ABC-1.md)", out)
        self.assertIn(
            "decisions/beta.md to projects/beta.md (1 lines, 1 to archive/decisions/beta.md)", out
        )

    def test_missing_task_files_are_stubs_with_the_source_mtime(self) -> None:
        self.move()
        for key in dv.WITHOUT_TASK:
            text = self.get(f"tasks/{key}.md")
            self.assertIn(f"jira: {key}", text)
            self.assertIn("## Decisions", text)
            self.assertIn(dv.line(f"first choice for {key}"), text)
            self.assertEqual(self.mtime(f"tasks/{key}.md"), dv.SOURCE_MTIME, key)

    def test_existing_owners_keep_their_mtime(self) -> None:
        self.move()
        for rel in ("tasks/ABC-1.md", "tasks/ABC-17.md", "projects/alpha.md", "projects/beta.md"):
            self.assertEqual(self.mtime(rel), dv.OWNER_MTIME, rel)

    def test_dry_run_writes_nothing(self) -> None:
        before = snapshot(self.sb.vault)
        out = self.move("--dry-run")
        self.assertEqual(snapshot(self.sb.vault), before)
        self.assertIn("decisions: would move decisions/ABC-1.md to tasks/ABC-1.md (", out)
        self.assertNotIn(": moved ", out)

    def test_second_run_moves_nothing(self) -> None:
        self.move()
        after = snapshot(self.sb.vault)
        out = self.move()
        self.assertIn("decisions: nothing to move", out)
        self.assertNotIn(": moved ", out)
        self.assertEqual(snapshot(self.sb.vault), after)

    def test_hidden_and_stray_files_are_skipped_with_a_reason(self) -> None:
        out = self.move()
        self.assertIn("decisions: decisions/gamma.md stays: the file is hidden (private)", out)
        self.assertIn("decisions: decisions/delta.md stays: it has text outside", out)
        self.assertTrue(self.exists("decisions/gamma.md"))
        self.assertTrue(self.exists("decisions/delta.md"))
        self.assertEqual(self.lines("projects/gamma.md"), [])
        self.assertEqual(self.lines("projects/delta.md"), [])

    def stale_entry(self, rel: str, matching: bool) -> None:
        sig = store.file_sig(self.vp(rel)) or [0, 0]
        sig = sig if matching else [sig[0] + 1, sig[1]]
        entry = {"stale": True, "since": "2026-10-01", "sig": sig}
        doc = {"v": 1, "generated": 1, "files": {rel: entry}}
        path = os.path.join(self.sb.state, "stale.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(doc, handle)

    def test_stale_mark_of_an_owner_survives_the_move(self) -> None:
        cfg = self.cfg
        self.stale_entry("projects/alpha.md", True)
        before = DiskVault(cfg).stale_mark("projects/alpha.md")
        self.assertTrue(before)
        self.move()
        self.assertEqual(DiskVault(cfg).stale_mark("projects/alpha.md"), before)
        self.assertNotEqual(DiskVault(cfg).stale_mark("projects/alpha.md"), "")

    def test_unmatched_or_missing_stale_entries_stay_so(self) -> None:
        cfg = self.cfg
        self.stale_entry("projects/alpha.md", False)
        self.move()
        self.assertEqual(DiskVault(cfg).stale_mark("projects/alpha.md"), "")
        with open(os.path.join(self.sb.state, "stale.json"), encoding="utf-8") as handle:
            files = json.load(handle)["files"]
        self.assertEqual(list(files), ["projects/alpha.md"])
        self.assertEqual(DiskVault(cfg).stale_mark("projects/beta.md"), "")

    def test_hidden_owner_is_skipped(self) -> None:
        self.put("tasks/ABC-1.md", "---\nstatus: archived\n---\n## Notes\n- x\n")
        before = self.get("tasks/ABC-1.md")
        out = self.move()
        self.assertIn("decisions/ABC-1.md stays: tasks/ABC-1.md is hidden (archived)", out)
        self.assertEqual(self.get("tasks/ABC-1.md"), before)
        self.assertTrue(self.exists("decisions/ABC-1.md"))

    def test_app_2_profile_owns_the_uppercased_legacy_file(self) -> None:
        self.move()
        self.assertEqual(self.lines("projects/app-2.md"), [dv.APP2_LINE])
        self.assertFalse(self.exists("decisions/APP-2.md"))
        self.assertFalse(self.exists("tasks/APP-2.md"))

    def test_path_links_are_counted_not_rewritten(self) -> None:
        note = "# Note\n\nSee [[decisions/ABC-1]] and [[decisions/payments-design]].\n"
        self.put("notes/link.md", note)
        out = self.move()
        self.assertIn("1 [[decisions/...]] path links point at moved files; not rewritten", out)
        self.assertEqual(self.get("notes/link.md"), note)

    def test_schema_step_five_to_six_does_the_same_move(self) -> None:
        toml = os.path.join(self.sb.vault, "juno-brain.toml")
        with open(toml, encoding="utf-8") as handle:
            text = handle.read()
        with open(toml, "w", encoding="utf-8") as handle:
            handle.write(migrate.write_schema(text, 5))
        self.assertEqual(self.juno("migrate")[0], 0)
        self.assertEqual(migrate.read_schema(self.get("juno-brain.toml")), 6)
        for key in dv.TICKETS:
            self.assertFalse(self.exists(f"decisions/{key}.md"))
            self.assertTrue(self.exists(f"tasks/{key}.md"))
        self.assertEqual(self.lines("projects/beta.md"), [dv.line("Use one queue")])
        self.assertTrue(self.exists("decisions/gamma.md"))
        self.assertEqual(self.mtime("tasks/ABC-20.md"), dv.SOURCE_MTIME)

    def test_doctor_warns_before_and_not_after(self) -> None:
        def messages() -> list[str]:
            _, out = self.juno("doctor", "--json", *self.sb.install_args())
            return [
                f"{c['status']} {c['detail']}"
                for c in json.loads(out)["checks"]
                if c["name"] == "decisions"
            ]

        before = messages()
        self.assertTrue(
            any("WARN decisions/ABC-1.md belongs in tasks/ABC-1.md" in m for m in before)
        )
        self.assertTrue(any("`juno migrate --decisions`" in m for m in before))
        self.move()
        after = messages()
        self.assertFalse([m for m in after if m.startswith("WARN")])
        self.assertTrue(any(m.startswith("INFO") and "decisions/gamma.md" in m for m in after))
        self.assertTrue(all("juno migrate" not in m for m in after if m.startswith("INFO")))

    def test_lineless_file_creates_no_task_file(self) -> None:
        out = self.move()
        for key in (dv.LINELESS, dv.LINELESS_HISTORY):
            self.assertFalse(self.exists(f"tasks/{key}.md"), key)
            self.assertFalse(self.exists(f"decisions/{key}.md"), key)
        self.assertIn(f"decisions: removed decisions/{dv.LINELESS}.md (no current lines)\n", out)
        self.assertIn(
            f"decisions: removed decisions/{dv.LINELESS_HISTORY}.md "
            f"(no current lines, 1 to archive/decisions/{dv.LINELESS_HISTORY}.md)",
            out,
        )
        archive = self.get(f"archive/decisions/{dv.LINELESS_HISTORY}.md")
        self.assertIn(dv.line("old choice for the lineless ticket", "2026-09-01"), archive)
        self.assertFalse(self.exists(f"archive/decisions/{dv.LINELESS}.md"))

    def test_lineless_dry_run_writes_nothing(self) -> None:
        before = snapshot(self.sb.vault)
        out = self.move("--dry-run")
        self.assertIn(f"decisions: would remove decisions/{dv.LINELESS}.md (no current", out)
        self.assertEqual(snapshot(self.sb.vault), before)

    def test_lineless_file_with_an_owner_is_moved_as_before(self) -> None:
        self.put("decisions/ABC-5.md", dv.decision_file("ABC-5", []))
        out = self.move()
        self.assertIn("moved decisions/ABC-5.md to tasks/ABC-5.md (0 lines)", out)
        self.assertFalse(self.exists("decisions/ABC-5.md"))

    def test_lineless_hidden_file_stays(self) -> None:
        text = self.get(f"decisions/{dv.LINELESS}.md")
        self.put(
            f"decisions/{dv.LINELESS}.md",
            text.replace("---\ntopic", "---\nvisibility: private\ntopic", 1),
        )
        out = self.move()
        self.assertIn(f"decisions/{dv.LINELESS}.md stays: the file is hidden", out)
        self.assertTrue(self.exists(f"decisions/{dv.LINELESS}.md"))

    def test_lineless_schema_step_removes_it_too(self) -> None:
        text = migrate.write_schema(self.get("juno-brain.toml"), 5)
        self.put("juno-brain.toml", text)
        self.assertEqual(self.juno("migrate")[0], 0)
        self.assertFalse(self.exists(f"tasks/{dv.LINELESS}.md"))
        self.assertFalse(self.exists(f"decisions/{dv.LINELESS}.md"))


class NoPrefixes(WriteCase):
    def test_ticket_shaped_files_stay_without_jira_prefixes(self) -> None:
        dv.build(self.sb.vault)
        code, out = self.juno("migrate", "--decisions")
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(self.vp("decisions/ABC-1.md")))
        self.assertFalse(os.path.exists(self.vp("tasks/ABC-18.md")))
        self.assertFalse(os.path.exists(self.vp("decisions/alpha.md")))

    def test_empty_decisions_folder_says_nothing_to_move(self) -> None:
        code, out = self.juno("migrate", "--decisions")
        self.assertEqual((code, out.strip()), (0, "decisions: nothing to move"))


if __name__ == "__main__":
    unittest.main()
