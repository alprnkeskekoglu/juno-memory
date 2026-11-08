import json
import os
import unittest
from typing import Any

from tests.support import SandboxCase


class SearchCli(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)
        self.write("notes/a.md", "# Alpha\n\nholdout group kural deployment\n")
        self.write("knowledge/k.md", "# Know\n\nholdout knowledge about deployment\n")
        self.write("notes/p.md", "---\nvisibility: private\n---\n# P\n\nholdout private matter\n")
        self.write("notes/old.md", "# Old\n\nholdout retiredterm\n")
        self.write("notes/new.md", "---\nsupersedes: notes/old.md\n---\n# New\n\nholdout newer\n")
        self.write("archive/handoffs/2026-01.md", "# Archived\n\nholdout archivedterm\n")

    def write(self, rel: str, text: str) -> None:
        path = os.path.join(self.sb.vault, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def search(self, *argv: str) -> tuple[int, dict[str, Any]]:
        code, out = self.sb.cli("search", *argv, "--json")
        return code, json.loads(out)

    def test_typo_is_corrected_and_announced(self) -> None:
        code, out = self.sb.cli("search", "holdotu")
        self.assertEqual(code, 0)
        self.assertIn('(searched "holdout" for "holdotu")', out)
        self.assertIn("notes/a.md", out)
        data = self.search("holdotu")[1]
        self.assertEqual(data["corrections"], {"holdotu": ["holdout"]})

    def test_private_retired_and_archive_are_excluded_by_default(self) -> None:
        paths = [r["relpath"] for r in self.search("holdout", "--limit", "20")[1]["results"]]
        self.assertNotIn("notes/p.md", paths)
        self.assertNotIn("notes/old.md", paths)
        self.assertFalse([p for p in paths if p.startswith("archive/")])
        self.assertIn("notes/new.md", paths)

    def test_archive_flag_finds_and_flags_archived_files(self) -> None:
        paths = [r["relpath"] for r in self.search("archivedterm")[1]["results"]]
        self.assertEqual(paths, [])
        data = self.search("archivedterm", "--archive")[1]
        self.assertEqual(
            [(r["relpath"], r.get("archive")) for r in data["results"]],
            [("archive/handoffs/2026-01.md", True)],
        )
        out = self.sb.cli("search", "archivedterm", "--archive")[1]
        self.assertIn("archive/handoffs/2026-01.md [archive] · Archived", out)

    def test_kinds_are_spread(self) -> None:
        for i in range(6):
            self.write(
                f"handoffs/h{i}.md",
                f"# H\n\n## 2026-10-25 23:2{i} · - · c · aaaaaaa{i}\nholdout holdout card {i}\n",
            )
        kinds = [
            r["relpath"].split("/")[0] for r in self.search("holdout", "--limit", "4")[1]["results"]
        ]
        self.assertEqual(len(kinds), 4)
        self.assertLessEqual(kinds.count("handoffs"), 2)

    def test_repo_and_ticket_filters_and_ticket_files_with_jira(self) -> None:
        self.write("tasks/ABC-9.md", "---\nrepos: [ash]\n---\n# T\n\nunrelated words\n")
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write('\n[jira]\nprefixes = ["ABC"]\n')
        data = self.search("ABC-9 holdout")[1]
        self.assertEqual(data["results"][0]["relpath"], "tasks/ABC-9.md")
        self.assertEqual(data["results"][0]["score"], 100.0)
        by_repo = self.search("unrelated", "--repo", "ash")[1]["results"]
        self.assertEqual([r["relpath"] for r in by_repo], ["tasks/ABC-9.md"])
        by_ticket = self.search("unrelated", "--ticket", "abc-9")[1]["results"]
        self.assertEqual([r["relpath"] for r in by_ticket], ["tasks/ABC-9.md"])

    def test_ticket_key_gets_no_boost_without_jira(self) -> None:
        self.write("tasks/ABC-9.md", "# T\n\nunrelated words\n")
        results = self.search("ABC-9 holdout")[1]["results"]
        self.assertNotIn(100.0, [r["score"] for r in results])

    def test_missing_vault_and_bad_limit_are_refused(self) -> None:
        self.assertEqual(self.sb.cli("search", "x", "--limit", "0")[0], 2)
        import shutil

        shutil.rmtree(self.sb.vault)
        self.assertEqual(self.sb.cli("search", "x")[0], 2)

    def test_no_results_text(self) -> None:
        self.assertEqual(self.sb.cli("search", "zzzzzzzzqqq"), (0, "no results\n"))

    def test_search_never_writes_the_vault(self) -> None:
        before = sorted(
            (os.path.join(b, f), os.stat(os.path.join(b, f)).st_mtime_ns)
            for b, _, files in os.walk(self.sb.vault)
            for f in files
        )
        self.sb.cli("search", "holdout")
        after = sorted(
            (os.path.join(b, f), os.stat(os.path.join(b, f)).st_mtime_ns)
            for b, _, files in os.walk(self.sb.vault)
            for f in files
        )
        self.assertEqual(before, after)
        self.assertTrue(os.path.exists(os.path.join(self.sb.state, "index.sqlite3")))
        self.assertTrue(os.path.exists(os.path.join(self.sb.state, "retired.json")))


if __name__ == "__main__":
    unittest.main()
