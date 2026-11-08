import argparse
import os
import unittest

from juno.adapters import migratesteps
from juno.cmd import migrate as cmd
from juno.core import migrate
from tests.support import REPO, SandboxCase


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

    def test_code_schema_is_three_with_two_steps(self) -> None:
        self.assertEqual(migrate.CODE_SCHEMA_VERSION, 3)
        self.assertEqual(sorted(migratesteps.STEPS), [1, 2])
        self.assertEqual(migrate.plan(1, 3, migratesteps.STEPS), [1, 2])

    def test_stale_exception_is_added_once_and_only_to_the_old_sentence(self) -> None:
        old = open(os.path.join(REPO, "vault-template", "core.md"), encoding="utf-8").read()
        old = old.replace(migrate.PRIVACY_NEW, migrate.PRIVACY_OLD)
        self.assertFalse(migrate.privacy_has_exception(old))
        new = migrate.add_stale_exception(old)
        self.assertTrue(migrate.privacy_has_exception(new))
        self.assertEqual(migrate.add_stale_exception(new), new)
        edited = old.replace(migrate.PRIVACY_OLD, "Write memory only when I say so.")
        self.assertEqual(migrate.add_stale_exception(edited), edited)
        self.assertFalse(migrate.privacy_has_exception(edited))
        self.assertEqual(old.replace(migrate.PRIVACY_OLD, ""), new.replace(migrate.PRIVACY_NEW, ""))


class Command(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        code, _ = self.sb.cli("init", *self.sb.install_args())
        self.assertEqual(code, 0)
        self.toml = os.path.join(self.sb.vault, "juno-brain.toml")

    def set_schema(self, version: int) -> None:
        with open(self.toml, encoding="utf-8") as handle:
            text = handle.read()
        with open(self.toml, "w", encoding="utf-8") as handle:
            handle.write(migrate.write_schema(text, version))

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
        self.set_schema(1)
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
        self.set_schema(1)

        def boom(vault: str) -> None:
            raise RuntimeError("step failed")

        with self.assertRaises(RuntimeError):
            self.run_cmd({1: boom}, 2)
        self.assertEqual(self.schema(), 1)

    def test_dry_run_changes_nothing(self) -> None:
        self.set_schema(1)
        called: list[int] = []
        self.assertEqual(self.run_cmd({1: lambda v: called.append(1)}, 2, dry_run=True), 0)
        self.assertEqual((called, self.schema()), ([], 1))
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "backups")))

    def test_missing_step_refused(self) -> None:
        self.set_schema(1)
        self.assertEqual(self.run_cmd({}, 2), 2)

    def test_real_step_adds_the_exception_to_a_schema_one_vault(self) -> None:
        core = os.path.join(self.sb.vault, "core.md")
        with open(core, encoding="utf-8") as handle:
            text = handle.read()
        with open(core, "w", encoding="utf-8") as handle:
            handle.write(text.replace(migrate.PRIVACY_NEW, migrate.PRIVACY_OLD))
        self.set_schema(1)
        self.assertEqual(self.sb.cli("migrate")[0], 0)
        self.assertEqual(self.schema(), 3)
        with open(core, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), text)
        self.assertEqual(self.sb.cli("migrate")[0], 0)
        with open(core, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), text)

    def read(self, rel: str) -> str:
        with open(os.path.join(self.sb.vault, rel), encoding="utf-8") as handle:
            return handle.read()

    def write(self, rel: str, text: str) -> None:
        with open(os.path.join(self.sb.vault, rel), "w", encoding="utf-8") as handle:
            handle.write(text)

    def make_schema_two(self) -> tuple[str, str]:
        rules = self.read(os.path.join("protocol", "rules.md"))
        core = self.read("core.md")
        self.assertIn(migrate.RULES_QUOTE_NEW, rules)
        self.assertIn(migrate.CAPS_NEW, core)
        self.write(
            os.path.join("protocol", "rules.md"),
            rules.replace(migrate.RULES_QUOTE_NEW, migrate.RULES_QUOTE_OLD),
        )
        self.write("core.md", core.replace(migrate.CAPS_NEW, migrate.CAPS_OLD))
        self.set_schema(2)
        return rules, core

    def test_step_two_rewrites_quote_rule_and_cap_sentence(self) -> None:
        rules, core = self.make_schema_two()
        self.assertEqual(self.sb.cli("migrate")[0], 0)
        self.assertEqual(self.schema(), 3)
        self.assertEqual(self.read(os.path.join("protocol", "rules.md")), rules)
        self.assertEqual(self.read("core.md"), core)
        backups = os.listdir(os.path.join(self.sb.state, "backups"))
        self.assertEqual([b for b in backups if b.endswith("-v2")], backups)
        old = os.path.join(self.sb.state, "backups", backups[0], "protocol", "rules.md")
        with open(old, encoding="utf-8") as handle:
            self.assertIn(migrate.RULES_QUOTE_OLD, handle.read())

    def test_step_two_is_idempotent(self) -> None:
        self.make_schema_two()
        steps = {2: migratesteps.quote_and_cap_text}
        migratesteps.quote_and_cap_text(self.sb.vault)
        once = (self.read(os.path.join("protocol", "rules.md")), self.read("core.md"))
        self.set_schema(2)
        self.assertEqual(self.run_cmd(steps, 3), 0)
        twice = (self.read(os.path.join("protocol", "rules.md")), self.read("core.md"))
        self.assertEqual(once, twice)

    def test_step_two_leaves_edited_lines_alone(self) -> None:
        self.make_schema_two()
        rules = self.read(os.path.join("protocol", "rules.md")).replace(
            migrate.RULES_QUOTE_OLD, "- The quote: whatever I typed."
        )
        core = self.read("core.md").replace(migrate.CAPS_OLD, "Stay small.")
        self.write(os.path.join("protocol", "rules.md"), rules)
        self.write("core.md", core)
        self.assertEqual(self.sb.cli("migrate")[0], 0)
        self.assertEqual(self.schema(), 3)
        self.assertEqual(self.read(os.path.join("protocol", "rules.md")), rules)
        self.assertEqual(self.read("core.md"), core)

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
