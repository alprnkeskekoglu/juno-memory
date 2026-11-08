import os
import tempfile
import unittest

from juno.adapters import interpreter
from juno.core import interp, tomlio


class Parsing(unittest.TestCase):
    def test_parse_version(self) -> None:
        self.assertEqual(interp.parse_version("(3, 15)\n"), (3, 15))
        self.assertEqual(interp.parse_version("sys.version_info(major=3, minor=9)"), None)
        self.assertIsNone(interp.parse_version(""))

    def test_parse_uv_list(self) -> None:
        out = (
            "cpython-3.12.16-macos-aarch64-none    /Users/x/.local/share/uv/python/cpython-3.12.16/bin/python3.12\n"
            "cpython-3.15.2-macos-aarch64-none     /opt/homebrew/bin/python3.15 -> /opt/homebrew/Cellar/x/bin/python3.15\n"
            "cpython-3.9.6-macos-aarch64-none      <download available>\n"
        )
        self.assertEqual(
            interp.parse_uv_list(out),
            [
                "/Users/x/.local/share/uv/python/cpython-3.12.16/bin/python3.12",
                "/opt/homebrew/bin/python3.15",
            ],
        )

    def test_python_names(self) -> None:
        self.assertTrue(interp.is_python_name("/x/python3"))
        self.assertTrue(interp.is_python_name("/x/python3.14"))
        self.assertFalse(interp.is_python_name("/x/python3-config"))
        self.assertFalse(interp.is_python_name("/x/python3.14-config"))


class Pick(unittest.TestCase):
    def test_first_source_with_floor_wins(self) -> None:
        sources = [
            ("homebrew", [("/b/py", (3, 15))]),
            ("pyenv", [("/p/py", (3, 16))]),
        ]
        self.assertEqual(interp.pick(sources), ("/b/py", (3, 15)))

    def test_below_floor_skipped(self) -> None:
        sources = [
            ("homebrew", [("/b/py", (3, 13))]),
            ("pyenv", []),
            ("uv", [("/u/py", (3, 14))]),
            ("system", [("/s/py", (3, 9))]),
        ]
        self.assertEqual(interp.pick(sources), ("/u/py", (3, 14)))

    def test_highest_within_source(self) -> None:
        sources = [("pyenv", [("/p/3.14", (3, 14)), ("/p/3.15", (3, 15)), ("/p/bad", None)])]
        self.assertEqual(interp.pick(sources), ("/p/3.15", (3, 15)))

    def test_none_when_nothing_qualifies(self) -> None:
        self.assertIsNone(interp.pick([("system", [("/s/py", (3, 9))])]))

    def test_refusal_is_one_line(self) -> None:
        line = interp.refusal("/usr/bin/python3", (3, 9))
        self.assertNotIn("\n", line)
        self.assertIn("3.14", line)
        self.assertIn("3.9", line)


class Discovery(unittest.TestCase):
    def test_override_must_meet_floor(self) -> None:
        env = {"JUNO_PYTHON": "/x/py"}
        self.assertEqual(interpreter.discover(env, lambda p: (3, 15)), ("/x/py", (3, 15)))
        self.assertIsNone(interpreter.discover(env, lambda p: (3, 13)))
        self.assertIsNone(interpreter.discover(env, lambda p: None))

    def test_gather_dedupes_by_realpath(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = os.path.realpath(os.path.join(tmp, "real-python3"))
            link = os.path.join(tmp, "python3")
            open(real, "w").close()
            os.symlink(real, link)
            sources = interpreter.gather({"PATH": tmp + os.pathsep + tmp})
            system = dict(sources)["system"]
            self.assertEqual(system.count(link), 1)
            self.assertEqual(len([p for p in system if os.path.realpath(p) == real]), 1)

    def test_probe_rejects_non_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "python3")
            open(path, "w").close()
            self.assertIsNone(interpreter.probe(path))

    def test_real_machine_has_a_usable_interpreter(self) -> None:
        found = interpreter.discover(dict(os.environ))
        if found is None:
            self.skipTest("no Python >= 3.14 on this machine")
        self.assertGreaterEqual(found[1], interp.FLOOR)
        self.assertEqual(interpreter.probe(found[0]), found[1])


class Toml(unittest.TestCase):
    def test_set_runtime_creates_first_table(self) -> None:
        out = tomlio.set_runtime("[caps]\ncore_chars = 3000\n", "/p/python3", "/root")
        self.assertTrue(out.startswith('[runtime]\npython = "/p/python3"\nplugin_root = "/root"\n'))
        self.assertTrue(tomlio.runtime_is_first_table(out))
        self.assertIn("[caps]\ncore_chars = 3000", out)

    def test_set_runtime_updates_only_runtime_keys(self) -> None:
        text = '[runtime]\npython = "/old"\nextra = 1\n\n[caps]\ncore_chars = 3000\n'
        out = tomlio.set_runtime(text, "/new", "/root")
        self.assertIn('python = "/new"', out)
        self.assertIn("extra = 1", out)
        self.assertIn('plugin_root = "/root"', out)
        self.assertIn("[caps]\ncore_chars = 3000", out)

    def test_idempotent(self) -> None:
        once = tomlio.set_runtime("", "/p", "/r")
        self.assertEqual(tomlio.set_runtime(once, "/p", "/r"), once)

    def test_rejects_unsafe_paths(self) -> None:
        with self.assertRaises(ValueError):
            tomlio.set_runtime("", '/p"x', "/r")

    def test_read_key_and_drop_table(self) -> None:
        text = tomlio.set_runtime("[caps]\ncore_chars = 3000\n", "/p", "/r")
        self.assertEqual(tomlio.read_key(text, "python"), "/p")
        dropped = tomlio.drop_table(text, "runtime")
        self.assertNotIn("runtime", dropped)
        self.assertIn("core_chars", dropped)
        self.assertEqual(tomlio.drop_table(tomlio.set_runtime("", "/p", "/r"), "runtime"), "")


if __name__ == "__main__":
    unittest.main()
