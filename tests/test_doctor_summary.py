import json
import re
import unittest
from typing import ClassVar
from unittest import mock

from juno.adapters import store
from juno.cmd import common
from juno.core import doctor_summary as dv
from juno.core import unreadable
from tests.support.write import WriteCase

NOW = 1_800_000_000.0
DAY = 86400


class RulesSize(unittest.TestCase):
    def test_line_shows_real_numbers_and_what_is_left(self) -> None:
        self.assertEqual(
            dv.rules_size_checks(3501, 2653, 6154, 4000, 5000, 9500),
            [
                (
                    "PASS",
                    "rules size",
                    "core 3501/4000, global 2653/5000, opening 6154/9500 "
                    "(3346 left for packs and the newest card)",
                )
            ],
        )

    def test_warns_when_less_than_the_floor_is_left(self) -> None:
        level, name, detail = dv.rules_size_checks(3900, 4800, 8800, 4000, 5000, 9500)[0]
        self.assertEqual((level, name), ("WARN", "rules size"))
        self.assertIn("opening 8800/9500 (700 left", detail)
        self.assertIn(f"under {dv.OPENING_LEFT_MIN}", detail)

    def test_floor_boundary(self) -> None:
        cap = 9500
        edge = cap - dv.OPENING_LEFT_MIN
        self.assertEqual(dv.rules_size_checks(1, 1, edge, 4000, 5000, cap)[0][0], "PASS")
        self.assertEqual(dv.rules_size_checks(1, 1, edge + 1, 4000, 5000, cap)[0][0], "WARN")

    def test_over_the_cap_never_shows_a_negative_remainder(self) -> None:
        detail = dv.rules_size_checks(5000, 6000, 11050, 4000, 5000, 9500)[0][2]
        self.assertIn("0 left", detail)
        self.assertNotIn("-", detail)

    def test_a_file_near_its_own_cap_is_left_to_the_budget_line(self) -> None:
        self.assertEqual(dv.rules_size_checks(3900, 1000, 4950, 4000, 5000, 9500)[0][0], "PASS")


class Snapshot(unittest.TestCase):
    def test_not_a_repo(self) -> None:
        out = dv.snapshot_checks(False, None, [], NOW)
        self.assertEqual([(s, n) for s, n, _ in out], [("INFO", "snapshot")])
        self.assertIn("not a git repository", out[0][2])

    def test_no_commit_yet(self) -> None:
        detail = dv.snapshot_checks(True, None, [], NOW)[0][2]
        self.assertIn("no snapshot commit yet", detail)
        self.assertIn("juno snapshot", detail)

    def test_age_and_no_remote(self) -> None:
        level, _, detail = dv.snapshot_checks(True, (int(NOW - 3 * DAY), "abcdef1234"), [], NOW)[0]
        self.assertEqual(level, "INFO")
        self.assertIn("3 days ago (abcdef1)", detail)
        self.assertIn("no remote configured", detail)

    def test_remote_is_not_proof(self) -> None:
        detail = dv.snapshot_checks(True, (int(NOW - 60), "abcdef1234"), ["origin", "b"], NOW)[0][2]
        self.assertIn("today", detail)
        self.assertIn("remote origin, b", detail)
        self.assertIn("does not prove the backup is current", detail)


class Unreadable(unittest.TestCase):
    def test_nothing_unreadable_says_nothing(self) -> None:
        self.assertEqual(dv.unreadable_checks([]), [])

    def test_names_the_first_few(self) -> None:
        names = [f"knowledge/{c}.md" for c in "abcde"]
        self.assertEqual(
            dv.unreadable_checks(names),
            [
                (
                    "WARN",
                    "unreadable",
                    "5 files could not be read: knowledge/a.md, knowledge/b.md, "
                    "knowledge/c.md (and 2 more); their checks above ignore them",
                )
            ],
        )

    def test_singular_and_shared_text(self) -> None:
        self.assertEqual(unreadable.describe(["x.md"]), "1 file could not be read: x.md")
        self.assertEqual(unreadable.describe([]), "")


class SettingsPaths(unittest.TestCase):
    OLD: ClassVar[dict[str, list[str]]] = {
        "additionalDirectories": ["/old/vault"],
        "allow": ["Read(/old/vault/**)"],
    }

    def test_nothing_recorded_for_another_vault_is_fine(self) -> None:
        self.assertEqual(dv.stale_path_checks({}, ["/new/vault"], "/new/vault"), [])

    def test_old_entries_with_the_new_ones_present_are_fine(self) -> None:
        self.assertEqual(dv.stale_path_checks(self.OLD, [], "/new/vault"), [])

    def test_old_entries_without_the_new_ones_warn_with_the_fix(self) -> None:
        out = dv.stale_path_checks(self.OLD, ["/new/vault", "Read(/new/vault/**)"], "/new/vault")
        self.assertEqual([(s, n) for s, n, _ in out], [("WARN", "settings paths")])
        self.assertIn("/old/vault", out[0][2])
        self.assertIn("lacks 2 for /new/vault", out[0][2])
        self.assertTrue(out[0][2].endswith("run juno init"))


class Collected(WriteCase):
    def doctor(self) -> dict[str, list[tuple[str, str]]]:
        _, out = self.juno("doctor", "--json", *self.sb.install_args())
        grouped: dict[str, list[tuple[str, str]]] = {}
        for check in json.loads(out)["checks"]:
            grouped.setdefault(check["name"], []).append((check["status"], check["detail"]))
        return grouped

    def test_rules_size_uses_the_injected_text(self) -> None:
        self.put("core.md", "# Core\n\n" + "x" * 100 + "\n")
        detail = self.doctor()["rules size"][0][1]
        match = re.fullmatch(
            r"core (\d+)/4000, global (\d+)/5000, opening (\d+)/9500 \((\d+) left for packs "
            r"and the newest card\)",
            detail,
        )
        assert match is not None, detail
        core, glob, opening, left = map(int, match.groups())
        self.assertEqual(core, len(("# Core\n\n" + "x" * 100).strip()))
        self.assertGreaterEqual(opening, core + glob)
        self.assertEqual(left, 9500 - opening)

    def test_snapshot_line_after_a_snapshot(self) -> None:
        self.assertEqual(self.juno("snapshot")[0], 0)
        status, detail = self.doctor()["snapshot"][0]
        self.assertEqual(status, "INFO")
        self.assertIn("today", detail)
        self.assertIn("no remote configured", detail)

    def test_stale_vault_path_in_settings(self) -> None:
        with open(self.sb.settings, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(self.sb.vault, text)
        self.assertNotIn("settings paths", self.doctor())
        manifest = common.manifest_path(self.cfg)
        for path in (self.sb.settings, manifest):
            with open(path, encoding="utf-8") as handle:
                moved = handle.read().replace(self.sb.vault, "/old/place/vault")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(moved)
        rows = self.doctor()["settings paths"]
        self.assertEqual(rows[0][0], "WARN")
        self.assertIn("/old/place/vault", rows[0][1])
        self.assertIn("juno init", rows[0][1])

    def test_unreadable_vault_files_are_counted_not_skipped(self) -> None:
        self.put("knowledge/bad.md", "# Bad\n\nbody\n")
        self.put("knowledge/worse.md", "# Worse\n\nbody\n")
        self.put("knowledge/fine.md", "# Fine\n\nbody\n")
        real = store.read_text

        def flaky(path: str) -> str:
            if path.endswith("knowledge/bad.md"):
                raise PermissionError(13, "denied", path)
            if path.endswith("knowledge/worse.md"):
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
            return real(path)

        with mock.patch.object(store, "read_text", flaky):
            grouped = self.doctor()
        status, detail = grouped["unreadable"][0]
        self.assertEqual(status, "WARN")
        self.assertIn("2 files could not be read", detail)
        self.assertIn("knowledge/bad.md", detail)
        self.assertIn("knowledge/worse.md", detail)
        self.assertNotIn("knowledge/fine.md", detail)
        self.assertEqual(len(grouped["unreadable"]), 1)

    def test_all_readable_has_no_unreadable_line(self) -> None:
        self.put("knowledge/fine.md", "# Fine\n\nbody\n")
        self.assertNotIn("unreadable", self.doctor())


if __name__ == "__main__":
    unittest.main()
