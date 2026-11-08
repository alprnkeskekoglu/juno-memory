import ast
import os
import tempfile
import unittest
from unittest import mock

from tests import run
from tests.support import REPO

DOC = os.path.join(REPO, "docs", "e2e.md")
HEADER = ["Feature", "Case", "Spec", "Last run", "Result", "Commit", "Gap"]
RESULTS = {"-", "PASS", "FAIL", "SKIP"}


def rows(path: str = DOC) -> list[list[str]]:
    found = []
    with open(path, encoding="utf-8") as handle:
        for line in handle.read().splitlines():
            if line.startswith("|") and not line.startswith("|---"):
                found.append([c.strip() for c in line.strip("|").split("|")])
    return found


def e2e_classes() -> set[str]:
    found = set()
    folder = os.path.join(REPO, "tests", "e2e")
    for name in sorted(os.listdir(folder)):
        if name.startswith("test_") and name.endswith(".py"):
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and any(
                    isinstance(item, ast.FunctionDef) and item.name.startswith("test_")
                    for item in node.body
                ):
                    found.add(f"tests/e2e/{name}::{node.name}")
    return found


class Table(unittest.TestCase):
    def test_header_and_shape(self) -> None:
        table = rows()
        self.assertEqual(table[0], HEADER)
        for row in table[1:]:
            self.assertEqual(len(row), len(HEADER), row)
            self.assertIn(row[4], RESULTS, row)

    def test_every_row_points_at_a_real_case(self) -> None:
        cases = e2e_classes()
        for row in rows()[1:]:
            self.assertIn(row[1], cases, row)

    def test_one_row_per_feature_and_per_case(self) -> None:
        body = rows()[1:]
        for column in (0, 1):
            seen = [row[column] for row in body]
            self.assertEqual(sorted({x for x in seen if seen.count(x) > 1}), [])

    def test_every_case_has_a_row(self) -> None:
        listed = {row[1] for row in rows()[1:]}
        self.assertEqual(sorted(e2e_classes() - listed), [])

    def test_every_case_is_marked_e2e(self) -> None:
        folder = os.path.join(REPO, "tests", "e2e")
        for name in sorted(os.listdir(folder)):
            if name.startswith("test_") and name.endswith(".py"):
                with open(os.path.join(folder, name), encoding="utf-8") as handle:
                    tree = ast.parse(handle.read())
                for node in tree.body:
                    if isinstance(node, ast.ClassDef) and node.name != "Case":
                        marks = [d.id for d in node.decorator_list if isinstance(d, ast.Name)]
                        self.assertIn("e2e", marks, f"{name}::{node.name}")


class Rewrite(unittest.TestCase):
    def test_runner_rewrites_only_the_run_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            target = os.path.join(tmp, "docs", "e2e.md")
            with open(DOC, encoding="utf-8") as src, open(target, "w", encoding="utf-8") as dst:
                dst.write(src.read())
            outcomes = {
                "tests.e2e.test_phase1.SessionStartInjection.test_p_session_with_override_sees_core_md": "PASS",
                "tests.e2e.test_phase1.UninstallRestoresSettings.test_a": "PASS",
                "tests.e2e.test_phase1.UninstallRestoresSettings.test_b": "FAIL",
            }
            with (
                mock.patch.object(run, "ROOT", tmp),
                mock.patch.object(run, "commit_label", lambda: "abc1234"),
            ):
                run.update_e2e_doc(outcomes)
            before, after = rows(DOC), rows(target)
            by_case = {r[1]: r for r in after[1:]}
            injected = by_case["tests/e2e/test_phase1.py::SessionStartInjection"]
            self.assertEqual((injected[4], injected[5]), ("PASS", "abc1234"))
            self.assertRegex(injected[3], r"^\d{4}-\d{2}-\d{2}$")
            self.assertEqual(
                by_case["tests/e2e/test_phase1.py::UninstallRestoresSettings"][4], "FAIL"
            )
            untouched = "tests/e2e/test_phase1.py::ExportImport"
            before_by_case = {r[1]: r for r in before[1:]}
            self.assertEqual(by_case[untouched][4], before_by_case[untouched][4])
            for old, new in zip(before, after, strict=True):
                self.assertEqual((old[0], old[1], old[2], old[6]), (new[0], new[1], new[2], new[6]))


if __name__ == "__main__":
    unittest.main()
