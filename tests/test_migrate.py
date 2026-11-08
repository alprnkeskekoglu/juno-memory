import argparse
import os
import unittest

from juno.cmd import migrate as cmd
from juno.core import migrate
from tests.support import SandboxCase


class Core(unittest.TestCase):
    def test_schema_read_write(self) -> None:
        text = "schema_version = 1\ncreated = 2026-01-01\n"
        self.assertEqual(migrate.read_schema(text), 1)
        self.assertEqual(
            migrate.write_schema(text, 2), "schema_version = 2\ncreated = 2026-01-01\n"
        )
        self.assertIsNone(migrate.read_schema("created = 2026-01-01\n"))
        self.assertTrue(migrate.write_schema("a = 1\n", 3).startswith("schema_version = 3\n"))

    def test_plan_is_one_version_at_a_time(self) -> None:
        steps = {1: lambda v: None, 2: lambda v: None}
        self.assertEqual(migrate.plan(1, 3, steps), [1, 2])
        self.assertEqual(migrate.plan(3, 3, steps), [])
        with self.assertRaises(ValueError):
            migrate.plan(1, 4, steps)

    def test_code_schema_is_one(self) -> None:
        self.assertEqual(migrate.CODE_SCHEMA_VERSION, 1)
        self.assertEqual(migrate.STEPS, {})


class Command(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        code, _ = self.sb.cli("init", *self.sb.install_args())
        self.assertEqual(code, 0)
        self.toml = os.path.join(self.sb.vault, "juno-brain.toml")

    def args(self, **kw: bool) -> argparse.Namespace:
        return argparse.Namespace(dry_run=kw.get("dry_run", False))

    def schema(self) -> int | None:
        with open(self.toml, encoding="utf-8") as handle:
            return migrate.read_schema(handle.read())

    def run_cmd(self, steps: dict[int, migrate.Step], target: int, **kw: bool) -> int:
        return cmd.run(self.args(**kw), self.sb.env, steps, target)

    def test_up_to_date(self) -> None:
        code, out = self.sb.cli("migrate")
        self.assertEqual(code, 0)
        self.assertIn("nothing to do", out)

    def test_steps_apply_in_order_with_backup(self) -> None:
        seen: list[int] = []

        def step1(vault: str) -> None:
            seen.append(1)
            with open(os.path.join(vault, "notes", "from-step-1.md"), "w") as handle:
                handle.write("x\n")

        def step2(vault: str) -> None:
            seen.append(2)
            self.assertEqual(self.schema(), 2)

        self.assertEqual(self.run_cmd({1: step1, 2: step2}, 3), 0)
        self.assertEqual(seen, [1, 2])
        self.assertEqual(self.schema(), 3)
        backups = os.listdir(os.path.join(self.sb.state, "backups"))
        self.assertEqual(sorted(b.split("-v")[1] for b in backups), ["1", "2"])
        first = next(b for b in backups if b.endswith("-v1"))
        self.assertFalse(
            os.path.exists(os.path.join(self.sb.state, "backups", first, "notes", "from-step-1.md"))
        )

    def test_failed_step_leaves_schema(self) -> None:
        def boom(vault: str) -> None:
            raise RuntimeError("step failed")

        with self.assertRaises(RuntimeError):
            self.run_cmd({1: boom}, 2)
        self.assertEqual(self.schema(), 1)

    def test_dry_run_changes_nothing(self) -> None:
        called: list[int] = []
        self.assertEqual(self.run_cmd({1: lambda v: called.append(1)}, 2, dry_run=True), 0)
        self.assertEqual((called, self.schema()), ([], 1))
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "backups")))

    def test_missing_step_refused(self) -> None:
        self.assertEqual(self.run_cmd({}, 2), 2)

    def test_newer_vault_refused(self) -> None:
        with open(self.toml, "w", encoding="utf-8") as handle:
            handle.write("schema_version = 9\n")
        code, out = self.sb.cli("migrate")
        self.assertEqual(code, 2)
        self.assertIn("newer", out)

    def test_no_vault(self) -> None:
        self.sb.env["JUNO_VAULT"] = os.path.join(self.sb.root, "elsewhere")
        self.assertEqual(self.sb.cli("migrate")[0], 2)


if __name__ == "__main__":
    unittest.main()
