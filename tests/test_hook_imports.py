import json
import os
import subprocess
import unittest

from tests.support import PYTHON, REPO, clean_env, subproc

COMMON = {
    "sqlite3",
    "difflib",
    "argparse",
    "unittest",
    "email",
    "http",
    "urllib",
    "ssl",
    "socket",
    "asyncio",
    "multiprocessing",
    "concurrent",
    "juno.cli",
    "juno.core.learn",
    "juno.core.search",
    "juno.core.eval",
    "juno.core.backlog",
    "juno.core.stale",
}
PER_EVENT = {
    "session_start": {"juno.core.cards"},
    "subagent_start": {"subprocess", "shutil", "juno.core.cards"},
    "user_prompt_submit": {"subprocess", "juno.core.cards"},
    "pre_tool_use": {"subprocess", "shutil", "juno.core.cards", "juno.core.packs"},
    "post_tool_use": {"juno.core.cards"},
    "post_tool_use_failure": {"subprocess", "shutil", "juno.core.packs", "juno.core.topics"},
    "stop": {"subprocess", "shutil", "juno.core.packs", "juno.core.topics"},
    "session_end": {"juno.core.packs", "juno.core.topics", "juno.core.cards", "secrets"},
}
PROBE = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("entry", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(json.dumps(sorted(sys.modules)))
"""


def loaded(event: str) -> set[str]:
    entry = os.path.join(REPO, "juno", "hooks", f"{event}.py")
    done = subprocess.run(
        [PYTHON, "-I", "-S", "-c", PROBE, entry],
        capture_output=True,
        text=True,
        env=clean_env(),
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return set(json.loads(done.stdout))


@subproc
class ImportBans(unittest.TestCase):
    def test_each_entry_respects_its_banned_list(self) -> None:
        for event, extra in PER_EVENT.items():
            with self.subTest(event):
                present = loaded(event)
                banned = {
                    m for m in present if m in COMMON | extra or m.split(".")[0] in COMMON | extra
                }
                self.assertEqual(sorted(banned), [], event)

    def test_every_entry_is_covered(self) -> None:
        hooks = os.path.join(REPO, "juno", "hooks")
        entries = {f[:-3] for f in os.listdir(hooks) if f.endswith(".py") and f != "__init__.py"}
        self.assertEqual(entries - {"card_run"}, set(PER_EVENT))

    def test_entries_import_the_config_reader_only(self) -> None:
        present = loaded("stop")
        self.assertIn("juno.config", present)
        self.assertIn("tomllib", present)
        self.assertNotIn("juno.cmd.init", present)


if __name__ == "__main__":
    unittest.main()
