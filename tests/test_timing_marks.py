import ast
import os
import unittest

from tests.support import REPO

CLOCKS = {"perf_counter", "perf_counter_ns", "monotonic", "monotonic_ns"}
BOUNDS = {"assertLess", "assertLessEqual", "assertGreater", "assertGreaterEqual"}


def unmarked(path: str) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    found = []
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        cls_marked = any(ast.unparse(d) == "timing" for d in cls.decorator_list)
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef) or not fn.name.startswith("test_"):
                continue
            marked = cls_marked or any(ast.unparse(d) == "timing" for d in fn.decorator_list)
            calls = {
                n.func.attr
                for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            }
            if calls & CLOCKS and calls & BOUNDS and not marked:
                found.append(f"{os.path.basename(path)}::{cls.name}.{fn.name}")
    return found


class TimingMarks(unittest.TestCase):
    def test_wall_clock_tests_run_in_the_serial_phase(self) -> None:
        tests = os.path.join(REPO, "tests")
        missing = []
        for name in sorted(os.listdir(tests)):
            if name.startswith("test_") and name.endswith(".py"):
                missing += unmarked(os.path.join(tests, name))
        self.assertEqual(missing, [], "mark these @timing so they run serially")


if __name__ == "__main__":
    unittest.main()
