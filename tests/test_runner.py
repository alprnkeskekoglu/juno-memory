import os
import tempfile
import unittest
from unittest import mock

from tests import run
from tests.support import REPO, mark, subproc, timing


def write(root: str, rel: str, text: str = "") -> None:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = self.tmp.name
        for pkg in ("juno", "juno/core", "tests"):
            write(root, f"{pkg}/__init__.py")
        write(root, "juno/core/leaf.py", "VALUE = 1\n")
        write(root, "juno/core/mid.py", "from juno.core import leaf\n")
        write(root, "juno/top.py", "from .core import mid\nimport juno.core.leaf\n")
        write(root, "juno/lazy.py", "VALUE = 2\n")
        write(root, "juno/dispatch.py", "import importlib\n")
        write(root, "tests/test_top.py", "from juno import top\n")
        write(root, "tests/test_leaf.py", "from juno.core.leaf import VALUE\n")
        write(root, "tests/test_lazy_target.py", "import importlib\n")
        write(root, "tests/test_orphan.py", "import unittest\n")
        write(root, "tests/test_doc.py", "import unittest\n")
        write(
            root,
            "pyproject.toml",
            '[tool.juno-tests.inputs]\n"templates/*" = ["test_doc"]\n"docs/skip.md" = []\n'
            '[tool.juno-tests.targets]\n"tests.test_lazy_target" = ["juno.lazy"]\n',
        )
        patches = [
            mock.patch.object(run, "ROOT", root),
            mock.patch.object(run, "HERE", os.path.join(root, "tests")),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)


class Graph(Fixture):
    def test_module_names(self) -> None:
        self.assertEqual(run.module_of("juno/core/leaf.py"), "juno.core.leaf")
        self.assertEqual(run.module_of("juno/core/__init__.py"), "juno.core")
        self.assertEqual(run.module_of("tests/test_top.py"), "tests.test_top")

    def test_imports_cover_relative_from_and_parents(self) -> None:
        graph = run.build_graph()
        self.assertIn("juno.core.mid", graph["juno.top"])
        self.assertIn("juno.core.leaf", graph["juno.top"])
        self.assertIn("juno.core", graph["juno.core.mid"])
        self.assertIn("juno.core.leaf", graph["juno.core.mid"])
        self.assertIn("juno", graph["juno.core.mid"])

    def test_declared_targets_are_edges(self) -> None:
        self.assertIn("juno.lazy", run.build_graph()["tests.test_lazy_target"])

    def test_transitive_selection(self) -> None:
        chosen, _ = run.select(["juno/core/leaf.py"])
        self.assertEqual(chosen, {"tests.test_top", "tests.test_leaf"})

    def test_importer_only_selection(self) -> None:
        chosen, _ = run.select(["juno/top.py"])
        self.assertEqual(chosen, {"tests.test_top"})

    def test_dispatch_target_selects_its_declaring_test(self) -> None:
        chosen, _ = run.select(["juno/lazy.py"])
        self.assertEqual(chosen, {"tests.test_lazy_target"})

    def test_changed_test_selects_itself(self) -> None:
        chosen, _ = run.select(["tests/test_leaf.py"])
        self.assertEqual(chosen, {"tests.test_leaf"})

    def test_input_map(self) -> None:
        self.assertEqual(run.select(["templates/a.txt"])[0], {"tests.test_doc"})
        self.assertEqual(run.select(["docs/skip.md"])[0], set())

    def test_full_suite_triggers(self) -> None:
        for path in (
            "tests/run.py",
            "pyproject.toml",
            "tests/support/__init__.py",
            "tests/fixtures/a.json",
        ):
            chosen, reasons = run.select([path])
            self.assertIsNone(chosen, path)
            self.assertTrue(reasons)

    def test_unmapped_file_selects_everything(self) -> None:
        self.assertIsNone(run.select(["README.md"])[0])
        self.assertIsNone(run.select(["templates2/a.txt"])[0])

    def test_unreachable_test_is_reported(self) -> None:
        self.assertIn("tests.test_orphan", run.unreachable())
        self.assertNotIn("tests.test_doc", run.unreachable())
        self.assertNotIn("tests.test_lazy_target", run.unreachable())
        self.assertNotIn("tests.test_top", run.unreachable())

    def test_deleted_module_still_selects_importers(self) -> None:
        os.unlink(os.path.join(self.tmp.name, "juno/core/leaf.py"))
        chosen, _ = run.select(["juno/core/leaf.py"])
        self.assertEqual(chosen, {"tests.test_top", "tests.test_leaf"})


class RealRepo(unittest.TestCase):
    def test_no_unreachable_tests(self) -> None:
        self.assertEqual(run.unreachable(), [])

    def test_one_module_change_selects_a_narrow_set(self) -> None:
        chosen, _ = run.select(["juno/core/inject.py"])
        assert chosen is not None
        self.assertIn("tests.test_inject", chosen)
        self.assertNotIn("tests.test_config", chosen)
        self.assertLess(len(chosen), len(run.test_modules()))

    def test_cli_verb_change_reaches_cli_tests_through_declared_dispatch(self) -> None:
        chosen, _ = run.select(["juno/cmd/doctor.py"])
        assert chosen is not None
        self.assertIn("tests.test_doctor", chosen)
        self.assertIn("tests.test_cli", chosen)

    def test_hook_entry_change_reaches_the_subprocess_tests(self) -> None:
        chosen, _ = run.select(["juno/hooks/stop.py"])
        assert chosen is not None
        self.assertIn("tests.test_launcher", chosen)
        self.assertIn("tests.test_hook_imports", chosen)

    def test_template_change_reaches_template_and_init_tests(self) -> None:
        chosen, _ = run.select(["vault-template/core.md"])
        assert chosen is not None
        self.assertEqual({"tests.test_vault_template", "tests.test_init"} - chosen, set())

    def test_hooks_json_and_bin_are_mapped(self) -> None:
        for path in ("hooks/hooks.json", "bin/juno-hook", ".claude-plugin/plugin.json"):
            chosen, _ = run.select([path])
            self.assertTrue(chosen, path)

    def test_package_init_change_selects_importers(self) -> None:
        chosen, _ = run.select(["juno/__init__.py"])
        assert chosen is not None
        self.assertIn("tests.test_cli", chosen)

    def test_every_test_module_is_importable_by_name(self) -> None:
        self.assertIn("tests.test_runner", run.test_modules())
        self.assertTrue(all(m.startswith("tests.test_") for m in run.test_modules()))
        self.assertTrue(all(m.startswith("tests.e2e.test_") for m in run.test_modules(e2e=True)))


class Marks(unittest.TestCase):
    def test_marks_are_visible_on_methods_and_classes(self) -> None:
        @mark("custom")
        class Sample(unittest.TestCase):
            @timing
            def test_slow(self) -> None:
                pass

            def test_fast(self) -> None:
                pass

        self.assertEqual(run.marks_of(Sample("test_slow")), {"custom", "timing"})
        self.assertEqual(run.marks_of(Sample("test_fast")), {"custom"})


@subproc
class Execution(unittest.TestCase):
    def test_worker_defers_timing_tests_and_runs_them_serially(self) -> None:
        done = run.run_worker("tests.probes.good", "", None, False)
        self.assertTrue(done["ok"], done["report"])
        self.assertEqual(done["tests"], 1)
        self.assertEqual(done["deferred"], ["tests.probes.good.Probe.test_timing"])
        serial = run.run_worker(
            "tests.probes.good", "", ["tests.probes.good.Probe.test_timing"], True
        )
        self.assertEqual((serial["ok"], serial["tests"]), (True, 1))

    def test_worker_reports_failures_and_pattern_filter(self) -> None:
        failing = run.run_worker("tests.probes.bad", "", None, False)
        self.assertFalse(failing["ok"])
        self.assertIn("deliberate", str(failing["report"]))
        self.assertEqual(failing["outcomes"], {"tests.probes.bad.Probe.test_fails": "FAIL"})
        self.assertEqual(run.run_worker("tests.probes.bad", "no_such", None, False)["tests"], 0)

    def test_crashed_worker_is_a_failure(self) -> None:
        done = run.run_worker("tests.does_not_exist", "", None, False)
        self.assertFalse(done["ok"])

    def test_repo_root_is_the_real_one(self) -> None:
        self.assertEqual(os.path.realpath(run.ROOT), os.path.realpath(REPO))


if __name__ == "__main__":
    unittest.main()
