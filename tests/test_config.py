import os
import tempfile
import unittest

from juno import config, paths

SPEC_KEYS = """
runtime.python runtime.plugin_root paths.vault paths.state paths.claude_dir
language.preset language.correction_cues language.user_cues language.self_cues language.stopwords
jira.base_url jira.prefixes jira.projects jira.no_ticket_branches repos.roots repos.overview_roots
repos.exclude caps.hook_output_chars caps.core_chars caps.global_rules_chars caps.global_rule_lines
caps.scoped_rule_lines caps.overview_chars caps.pack_chars caps.max_full_packs caps.last_message_chars
caps.stub_excerpt_chars caps.quote_min_chars caps.run_max caps.rule_max_chars caps.overview_cards caps.pack_cards
caps.pack_threads_chars caps.pack_knowledge_chars caps.subagent_chars caps.subagent_global_rules_chars
thresholds.meaningful_tool_calls thresholds.meaningful_user_turns thresholds.dirty_user_turns
thresholds.dirty_tool_calls thresholds.reminder_spacing_turns thresholds.active_ticket_days
thresholds.thread_stale_days thresholds.stale_days thresholds.stub_quiet_seconds
thresholds.hook_error_repeat thresholds.hook_error_window_hours retention.ledger_days
retention.backup_days retention.shadow_days retention.asks_days retention.usage_days retention.arm_segments_days retention.error_log_bytes privacy.allow_prefixes shadow.enabled shadow.budget_ms
handoff.mode handoff.model handoff.budget_usd handoff.daily_budget_usd handoff.min_run_usd
handoff.timeout_seconds handoff.digest_chars handoff.extract handoff.claude_bin handoff.launch_path
threads.scan threads.stale_days threads.gh threads.gh_owner threads.gh_calls
threads.gh_timeout_seconds links.linked_block keys.weight keys.model packs.purpose decay.window_days
self.max_lines self.max_chars
stale.auto_fix stale.weekday stale.start_hour stale.end_hour stale.model stale.file_budget_usd
stale.run_budget_usd stale.timeout_seconds stale.max_files stale.max_changes_per_file
stale.max_changes_per_run stale.max_lines_per_file stale.branch_order stale.fetch
stale.busy_minutes
""".split()


def load_text(text: str, env: dict[str, str] | None = None) -> config.Config:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "juno.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return config.load(env or {"HOME": tmp}, path)


class Defaults(unittest.TestCase):
    def test_every_spec_key_has_a_default(self) -> None:
        have = {f"{s}.{k}" for s, keys in config.DEFAULTS.items() for k in keys}
        self.assertEqual(sorted(set(SPEC_KEYS) - have), [])
        self.assertEqual(sorted(have - set(SPEC_KEYS)), [])

    def test_spec_values(self) -> None:
        cfg = load_text("")
        self.assertEqual(cfg.get_int("caps", "hook_output_chars"), 9500)
        self.assertEqual(cfg.get_str("language", "preset"), "none")
        self.assertTrue(cfg.get_bool("shadow", "enabled"))
        self.assertEqual(cfg.get_int("retention", "shadow_days"), 365)
        self.assertEqual(cfg.get_list("jira", "prefixes"), [])
        self.assertEqual(cfg.warnings, [])

    def test_hook_cap_below_claude_limit(self) -> None:
        self.assertLess(config.DEFAULTS["caps"]["hook_output_chars"], 10000)


class Overrides(unittest.TestCase):
    def test_file_overrides_defaults(self) -> None:
        cfg = load_text("[caps]\ncore_chars = 3000\n")
        self.assertEqual(cfg.get_int("caps", "core_chars"), 3000)
        self.assertEqual(cfg.get_int("caps", "pack_chars"), 4500)

    def test_wrong_type_falls_back_with_warning(self) -> None:
        cfg = load_text('[caps]\ncore_chars = "big"\nmax_full_packs = true\n')
        self.assertEqual(cfg.get_int("caps", "core_chars"), 4000)
        self.assertEqual(cfg.get_int("caps", "max_full_packs"), 4)
        self.assertEqual(len(cfg.warnings), 2)

    def test_int_is_accepted_for_float(self) -> None:
        self.assertEqual(
            load_text("[handoff]\nbudget_usd = 1\n").get_float("handoff", "budget_usd"), 1.0
        )

    def test_broken_toml_uses_defaults(self) -> None:
        cfg = load_text("[caps\n")
        self.assertEqual(cfg.get_int("caps", "core_chars"), 4000)
        self.assertEqual(len(cfg.warnings), 1)

    def test_missing_file_is_fine(self) -> None:
        cfg = config.load({"HOME": "/nonexistent"}, "/nonexistent/juno.toml")
        self.assertEqual(cfg.warnings, [])

    def test_non_table_section_warns(self) -> None:
        self.assertEqual(len(load_text('caps = "x"\n').warnings), 1)


class Language(unittest.TestCase):
    def test_none_preset_is_english(self) -> None:
        cues = load_text("").language_list("correction_cues")
        self.assertIn("never", cues)
        self.assertNotIn("asla", cues)

    def test_tr_preset(self) -> None:
        cfg = load_text('[language]\npreset = "tr"\n')
        self.assertIn("asla", cfg.language_list("correction_cues"))
        self.assertIn("hayir", cfg.language_list("user_cues"))

    def test_explicit_list_wins(self) -> None:
        cfg = load_text('[language]\npreset = "tr"\ncorrection_cues = ["xyz"]\n')
        self.assertEqual(cfg.language_list("correction_cues"), ["xyz"])
        self.assertIn("haklisin", cfg.language_list("self_cues"))

    def test_unknown_preset_warns(self) -> None:
        cfg = load_text('[language]\npreset = "xx"\n')
        self.assertEqual(cfg.get_str("language", "preset"), "none")
        self.assertEqual(len(cfg.warnings), 1)


class Resolution(unittest.TestCase):
    def test_env_beats_config_for_vault_and_state(self) -> None:
        cfg = load_text('[paths]\nvault = "/a/v"\nstate = "/a/s"\n', {"HOME": "/h"})
        self.assertEqual((cfg.vault, cfg.state), ("/a/v", "/a/s"))
        cfg = load_text(
            '[paths]\nvault = "/a/v"\nstate = "/a/s"\n',
            {"HOME": "/h", "JUNO_VAULT": "/e/v", "JUNO_STATE": "/e/s"},
        )
        self.assertEqual((cfg.vault, cfg.state), ("/e/v", "/e/s"))

    def test_defaults_resolve_under_home(self) -> None:
        cfg = load_text("", {"HOME": "/h"})
        self.assertEqual(cfg.vault, "/h/juno-vault")
        self.assertEqual(cfg.claude_dir, "/h/.claude")

    def test_repo_roots_only_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, "code"))
            cfg = load_text("", {"HOME": tmp})
            self.assertEqual(cfg.repo_roots(), [os.path.join(tmp, "code")])
            self.assertEqual(cfg.overview_roots(), [os.path.join(tmp, "code")])

    def test_repo_roots_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_text("", {"HOME": "/h", "JUNO_REPO_ROOTS": tmp + os.pathsep + "/missing"})
            self.assertEqual(cfg.repo_roots(), [tmp])

    def test_launch_path_default(self) -> None:
        self.assertEqual(load_text("").get_str("handoff", "launch_path"), config.LAUNCH_PATH)


class Paths(unittest.TestCase):
    def test_config_path_precedence(self) -> None:
        self.assertEqual(paths.config_path({"HOME": "/h"}), "/h/.config/juno/juno.toml")
        self.assertEqual(
            paths.config_path({"HOME": "/h", "XDG_CONFIG_HOME": "/x"}), "/x/juno/juno.toml"
        )
        self.assertEqual(paths.config_path({"HOME": "/h", "JUNO_CONFIG": "/c/j.toml"}), "/c/j.toml")

    def test_state_dir_platform_default(self) -> None:
        state = paths.default_state_dir({"HOME": "/h", "XDG_STATE_HOME": "/xs"})
        self.assertTrue(state.endswith("Application Support/juno") or state == "/xs/juno")


if __name__ == "__main__":
    unittest.main()
