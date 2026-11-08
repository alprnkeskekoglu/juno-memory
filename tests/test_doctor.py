import json
import os
import re
import unittest

from tests.support import SandboxCase, settings_variants


class Doctor(SandboxCase):
    def doctor(self, *extra: str) -> tuple[int, str]:
        return self.sb.cli("doctor", *self.sb.install_args(), *extra)

    def installed(self) -> None:
        self.sb.write_settings(settings_variants()["indent2"])
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)

    def statuses(self) -> dict[str, str]:
        code, out = self.doctor("--json")
        data = json.loads(out)
        self.assertEqual(code, 0 if data["ok"] else 1)
        return {c["name"]: c["status"] for c in data["checks"]}

    def test_clean_after_init(self) -> None:
        self.installed()
        code, out = self.doctor()
        self.assertEqual(code, 0, out)
        self.assertNotIn("FAIL", out)
        states = self.statuses()
        for name in (
            "interpreter",
            "readers",
            "vault",
            "state",
            "shim",
            "settings merge",
            "plugin root",
        ):
            self.assertEqual(states[name], "PASS", name)
        self.assertEqual(states["plugin"], "WARN")

    def test_leftover_card_holdout_is_a_warning(self) -> None:
        self.installed()
        self.assertNotIn("card_holdout", self.statuses())
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write("\n[ab]\ncard_holdout = 0.5\n")
        code, out = self.doctor()
        self.assertEqual(code, 0, out)
        self.assertIn("WARN card_holdout: [ab] card_holdout is ignored; use juno arm", out)

    def test_before_init_fails_with_hint(self) -> None:
        code, out = self.doctor()
        self.assertEqual(code, 1)
        self.assertIn("run juno init", out)

    def test_old_interpreter_is_one_line(self) -> None:
        self.installed()
        fake = os.path.join(self.sb.root, "py39")
        with open(fake, "w", encoding="utf-8") as handle:
            handle.write('#!/bin/sh\necho "(3, 9)"\n')
        os.chmod(fake, 0o755)
        with open(self.sb.config, encoding="utf-8") as handle:
            text = re.sub(r'^python = ".*"$', f'python = "{fake}"', handle.read(), flags=re.M)
        with open(self.sb.config, "w", encoding="utf-8") as handle:
            handle.write(text)
        code, out = self.doctor()
        self.assertEqual(code, 1)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("FAIL interpreter: Juno needs Python >= 3.14"))

    def test_readers_disagree_is_fail(self) -> None:
        self.installed()
        with open(self.sb.config, encoding="utf-8") as handle:
            text = re.sub(
                r'^python = "(.*)"$', r'python = "\1" # comment', handle.read(), flags=re.M
            )
        with open(self.sb.config, "w", encoding="utf-8") as handle:
            handle.write(text)
        self.assertEqual(self.statuses()["readers"], "FAIL")

    def test_missing_settings_entry_is_fail(self) -> None:
        self.installed()
        data = json.loads(self.sb.read_settings() or b"")
        data["permissions"]["allow"] = []
        self.sb.write_settings(json.dumps(data).encode())
        self.assertEqual(self.statuses()["settings merge"], "FAIL")

    def test_auto_memory_warning(self) -> None:
        self.installed()
        data = json.loads(self.sb.read_settings() or b"")
        data["autoMemoryEnabled"] = True
        self.sb.write_settings(json.dumps(data).encode())
        self.assertEqual(self.statuses()["autoMemoryEnabled"], "WARN")

    def test_vault_schema_mismatch(self) -> None:
        self.installed()
        with open(os.path.join(self.sb.vault, "juno-brain.toml"), "w", encoding="utf-8") as handle:
            handle.write("schema_version = 5\n")
        self.assertEqual(self.statuses()["vault"], "FAIL")

    def test_hook_cap_must_stay_below_claude_limit(self) -> None:
        self.installed()
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write("\n[caps]\nhook_output_chars = 12000\n")
        self.assertEqual(self.statuses()["hook cap"], "FAIL")

    def test_config_type_warning(self) -> None:
        self.installed()
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write('\n[caps]\ncore_chars = "x"\n')
        self.assertEqual(self.statuses()["config"], "WARN")

    def test_state_mode_warning(self) -> None:
        self.installed()
        os.chmod(self.sb.state, 0o755)
        self.assertEqual(self.statuses()["state"], "WARN")

    def test_missing_shim_warns(self) -> None:
        self.installed()
        os.unlink(os.path.join(self.sb.bin, "juno"))
        self.assertEqual(self.statuses()["shim"], "WARN")


if __name__ == "__main__":
    unittest.main()
