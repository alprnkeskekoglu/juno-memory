import json
import subprocess
import unittest

from juno import __version__, cli
from tests.support import PYTHON, REPO, SandboxCase, clean_env, subproc


class Dispatch(SandboxCase):
    def test_version(self) -> None:
        self.assertEqual(self.sb.cli("version"), (0, f"juno {__version__}\n"))

    def test_help_lists_every_verb(self) -> None:
        code, out = self.sb.cli("--help")
        self.assertEqual(code, 0)
        for verb in cli.VERBS:
            self.assertIn(verb, out)
        self.assertEqual(self.sb.cli()[0], 0)

    def test_unknown_verb_exits_two(self) -> None:
        self.assertEqual(self.sb.cli("frobnicate")[0], 2)

    def test_phase1_verbs_exist(self) -> None:
        phase1 = {
            "init",
            "uninstall",
            "doctor",
            "export",
            "import",
            "migrate",
            "version",
            "dashboard",
        }
        self.assertLessEqual(phase1, set(cli.VERBS))

    def test_doctor_json_shape(self) -> None:
        code, out = self.sb.cli("doctor", "--json", *self.sb.install_args())
        data = json.loads(out)
        self.assertEqual(code, 1)
        self.assertFalse(data["ok"])
        self.assertTrue(all(set(c) == {"status", "name", "detail"} for c in data["checks"]))

    def test_verb_help_exits_zero(self) -> None:
        for verb in cli.VERBS:
            with self.subTest(verb), self.assertRaises(SystemExit) as raised:
                cli.main([verb, "-h"], self.sb.env)
            self.assertEqual(raised.exception.code, 0)


@subproc
class Lazy(unittest.TestCase):
    def test_version_does_not_import_other_verbs(self) -> None:
        code = (
            "import sys, json; sys.path.insert(0, %r); from juno import cli; "
            "cli.main(['version'], {}); print(json.dumps([m for m in sys.modules if m.startswith('juno.cmd')]))"
            % REPO
        )
        done = subprocess.run(
            [PYTHON, "-I", "-S", "-c", code], capture_output=True, text=True, env=clean_env()
        )
        loaded = json.loads(done.stdout.splitlines()[-1])
        self.assertEqual(sorted(loaded), ["juno.cmd", "juno.cmd.version"])


if __name__ == "__main__":
    unittest.main()
