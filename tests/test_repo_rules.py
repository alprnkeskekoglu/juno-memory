import os
import stat
import tempfile
import unittest
import warnings

from tests.support import REPO
from tests.support.banned import LOCAL_FILE, local_banned, read_list

SKIP_DIRS = {".git", "__pycache__", ".venv", ".mypy_cache", ".ruff_cache", ".claude", ".idea"}
LINE_LIMIT = 600
NAMES = ("/opt/" + "homebrew", *local_banned())
BANNED = (*NAMES, "/Users/", "/home/")
EXEMPT = {
    "juno/config.py": "launch_path default (spec 15.1)",
    "juno/adapters/interpreter.py": "Homebrew discovery globs (spec 13.2)",
    "bin/juno": "interpreter fallback before init (spec 13.2)",
}
TEST_EXEMPT = {
    "tests/test_interp.py": "fixtures of the discovery lists (spec 13.2)",
    "tests/test_repo_rules.py": "the banned list itself",
    "tests/test_skills.py": "its own banned list",
    "tests/test_vault_template.py": "its own banned pattern",
}
SCANNED = (
    "juno",
    "bin",
    "hooks",
    "skills",
    "vault-template",
    ".claude-plugin",
    ".githooks",
    "ui/hooks",
    "ui/.claude-plugin",
)
SCANNED_FIXTURES = ("tests", "ui/test")


def repo_files(top: str = "") -> list[str]:
    found = []
    for base, dirs, files in os.walk(os.path.join(REPO, top)):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        found += [os.path.join(base, f) for f in files]
    return sorted(found)


def long_modules(root: str) -> list[tuple[str, int]]:
    found = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(base, name), encoding="utf-8") as handle:
                    count = sum(1 for _ in handle)
                if count > LINE_LIMIT:
                    found.append((os.path.relpath(os.path.join(base, name), root), count))
    return found


class ModuleSize(unittest.TestCase):
    def test_repo_modules_stay_short(self) -> None:
        for name, count in long_modules(REPO):
            warnings.warn(f"{name} has {count} lines (limit {LINE_LIMIT})", stacklevel=1)
        self.assertEqual([m for m in long_modules(REPO) if m[1] > 2 * LINE_LIMIT], [])

    def test_checker_flags_a_long_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "big.py"), "w", encoding="utf-8") as handle:
                handle.write("x = 1\n" * (LINE_LIMIT + 1))
            with open(os.path.join(tmp, "ok.py"), "w", encoding="utf-8") as handle:
                handle.write("x = 1\n" * LINE_LIMIT)
            self.assertEqual(long_modules(tmp), [("big.py", LINE_LIMIT + 1)])

    def test_no_module_is_over_the_limit_today(self) -> None:
        self.assertEqual(long_modules(REPO), [])


class Hygiene(unittest.TestCase):
    def test_files_end_with_a_newline(self) -> None:
        for path in repo_files():
            if os.path.getsize(path) == 0 or path.endswith((".pyc", ".sqlite3")):
                continue
            with open(path, "rb") as handle:
                data = handle.read()
            self.assertTrue(data.endswith(b"\n"), os.path.relpath(path, REPO))

    def test_no_user_names_or_absolute_paths_in_shipped_code(self) -> None:
        for top in SCANNED:
            for path in repo_files(top):
                rel = os.path.relpath(path, REPO)
                if rel in EXEMPT or path.endswith(".pyc"):
                    continue
                with open(path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
                for needle in BANNED:
                    self.assertNotIn(needle, text, f"{needle!r} in {rel}")

    def test_tests_and_ui_carry_no_user_names_or_company_fixtures(self) -> None:
        for top in SCANNED_FIXTURES:
            for path in repo_files(top):
                rel = os.path.relpath(path, REPO)
                if rel in TEST_EXEMPT or path.endswith((".pyc", ".sqlite3")):
                    continue
                with open(path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
                for needle in NAMES:
                    self.assertNotIn(needle, text, f"{needle!r} in {rel}")

    def test_exemptions_are_still_needed(self) -> None:
        for rel in EXEMPT:
            with open(os.path.join(REPO, rel), encoding="utf-8") as handle:
                text = handle.read()
            self.assertTrue(any(n in text for n in BANNED), f"{rel} no longer needs its exemption")
        with open(os.path.join(REPO, "tests/test_interp.py"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertTrue(any(n in text for n in NAMES), "tests/test_interp.py")

    def test_no_future_annotations_import(self) -> None:
        for path in repo_files("juno") + repo_files("tests"):
            if path.endswith(".py"):
                with open(path, encoding="utf-8") as handle:
                    lines = [line.strip() for line in handle]
                self.assertNotIn("from __future__ import annotations", lines, path)


class PreCommit(unittest.TestCase):
    def test_hook_script_runs_everything_and_is_not_enabled_by_the_test(self) -> None:
        path = os.path.join(REPO, ".githooks", "pre-commit")
        self.assertTrue(os.stat(path).st_mode & stat.S_IXUSR)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        for needle in (
            "tests/run.py",
            "ruff check",
            "ruff format --check",
            "mypy --strict juno",
            "e2e",
        ):
            self.assertIn(needle, text)
        self.assertTrue(text.startswith("#!/bin/sh\n"))

    def test_runner_can_install_the_hook(self) -> None:
        with open(os.path.join(REPO, "tests", "run.py"), encoding="utf-8") as handle:
            self.assertIn("core.hooksPath", handle.read())


class LocalBannedList(unittest.TestCase):
    def test_list_ignores_comments_and_blank_lines_and_folds_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "banned.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# note\n\nAcmeCorp\n  my-user  \n")
            self.assertEqual(read_list(path), ("acmecorp", "my-user"))
        self.assertEqual(read_list(os.path.join(tmp, "missing.txt")), ())

    def test_env_var_points_at_the_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "banned.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("secret-name\n")
            self.assertEqual(local_banned({"JUNO_BANNED_FILE": path}), ("secret-name",))

    def test_local_file_is_gitignored(self) -> None:
        with open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as handle:
            self.assertIn(LOCAL_FILE, handle.read().split())


class Colleague(unittest.TestCase):
    def test_doc_states_the_rule(self) -> None:
        with open(os.path.join(REPO, "docs", "colleague.md"), encoding="utf-8") as handle:
            text = handle.read().lower()
        self.assertIn("never copy a vault between people", text)
        self.assertIn("own vault", text)


if __name__ == "__main__":
    unittest.main()
