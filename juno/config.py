import copy
import os
import tomllib
from collections.abc import Mapping
from typing import Any

from juno import paths

LAUNCH_PATH = "~/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

DEFAULTS: dict[str, dict[str, Any]] = {
    "runtime": {"python": "", "plugin_root": ""},
    "paths": {"vault": paths.DEFAULT_VAULT, "state": "", "claude_dir": "~/.claude"},
    "language": {
        "preset": "none",
        "correction_cues": [],
        "user_cues": [],
        "self_cues": [],
        "stopwords": [],
    },
    "jira": {
        "base_url": "",
        "prefixes": [],
        "projects": [],
        "no_ticket_branches": ["develop", "master", "main", "HEAD"],
    },
    "repos": {
        "roots": ["~/projects", "~/code", "~/src"],
        "overview_roots": [],
        "exclude": [],
    },
    "caps": {
        "hook_output_chars": 9500,
        "core_chars": 4000,
        "global_rules_chars": 5000,
        "global_rule_lines": 80,
        "scoped_rule_lines": 25,
        "overview_chars": 3000,
        "pack_chars": 4500,
        "max_full_packs": 4,
        "last_message_chars": 1500,
        "stub_excerpt_chars": 400,
        "quote_min_chars": 20,
        "rule_max_chars": 300,
        "overview_cards": 2,
        "pack_cards": 2,
        "pack_threads_chars": 500,
        "pack_knowledge_chars": 300,
        "subagent_chars": 3000,
        "subagent_global_rules_chars": 2500,
    },
    "thresholds": {
        "meaningful_tool_calls": 15,
        "meaningful_user_turns": 5,
        "dirty_user_turns": 10,
        "dirty_tool_calls": 40,
        "reminder_spacing_turns": 3,
        "active_ticket_days": 14,
        "thread_stale_days": 30,
        "stale_days": 60,
        "stub_quiet_seconds": 1800,
        "hook_error_repeat": 3,
        "hook_error_window_hours": 24,
    },
    "retention": {
        "ledger_days": 7,
        "backup_days": 30,
        "shadow_days": 365,
        "asks_days": 365,
        "usage_days": 365,
        "arm_segments_days": 365,
        "error_log_bytes": 1048576,
    },
    "shadow": {"enabled": True, "budget_ms": 150},
    "handoff": {
        "mode": "end",
        "model": "sonnet",
        "budget_usd": 0.3,
        "daily_budget_usd": 2.0,
        "min_run_usd": 0.05,
        "timeout_seconds": 240,
        "digest_chars": 16000,
        "extract": True,
        "claude_bin": "",
        "launch_path": LAUNCH_PATH,
    },
    "threads": {
        "scan": True,
        "stale_days": 14,
        "gh": "gh",
        "gh_owner": "",
        "gh_calls": 6,
        "gh_timeout_seconds": 10,
    },
    "links": {"linked_block": False},
    "packs": {"purpose": False},
    "decay": {"window_days": 90},
    "keys": {"weight": 0.0, "model": ""},
    "self": {"max_lines": 12, "max_chars": 1200},
}

PRESETS: dict[str, dict[str, list[str]]] = {
    "none": {
        "correction_cues": [
            "don't",
            "do not",
            "always",
            "never",
            "stop doing",
            "prefer",
            "from now on",
        ],
        "user_cues": ["wrong", "again", "still", "not what", "don't", "stop", "instead"],
        "self_cues": [
            "you're right",
            "i was wrong",
            "my mistake",
            "actually",
            "turns out",
            "root cause",
            "the real cause",
        ],
        "stopwords": (
            "the and for with that this from have should always never dont don't please make sure"
            " when what there their they them then than into also just only more some your about"
            " will would could does doing done here where which while each every must like want"
            " need used using these those been being were"
        ).split(),
    },
    "tr": {
        "correction_cues": [
            "hayır",
            "yanlış",
            "bundan sonra",
            "her zaman",
            "asla",
            "istemiyorum",
            "olmasın",
            "kullanma",
            "yapma",
            "don't",
            "do not",
            "always",
            "never",
            "stop doing",
            "prefer",
        ],
        "user_cues": [
            "hayir",
            "yanlis",
            "degil",
            "olmadi",
            "olmuyor",
            "hala",
            "yine",
            "tekrar",
            "neden",
            "niye",
            "dur",
            "boyle degil",
            "hatali",
            "wrong",
            "again",
            "still",
            "not what",
            "don't",
            "stop",
            "instead",
        ],
        "self_cues": [
            "haklisin",
            "yanilmisim",
            "hatam",
            "yanlis yaptim",
            "aslinda",
            "meger",
            "asil sebep",
            "kok neden",
            "you're right",
            "i was wrong",
            "my mistake",
            "actually",
            "turns out",
            "root cause",
            "the real cause",
        ],
        "stopwords": (
            "bu su o bir ve ile icin olan olsun gibi daha veya degil lutfen simdi sonra bundan"
            " artik bunu sunu bana beni sana onlar kadar diye yani hicbir hep her zaman asla sey"
            " seyi"
            " sadece ama fakat the and for with that this from have should always never dont don't"
            " please make sure when what there their they them then than into also just only more"
            " some your about will would could does doing done here where which while each every"
            " must like want need used using these those been being were"
        ).split(),
    },
}

LANGUAGE_LISTS = ("correction_cues", "user_cues", "self_cues", "stopwords")


def type_ok(default: Any, value: Any) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, list):
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return False


class Config:
    def __init__(
        self,
        values: dict[str, dict[str, Any]],
        warnings: list[str],
        explicit: set[tuple[str, str]],
        path: str,
        env: Mapping[str, str],
    ) -> None:
        self.values = values
        self.warnings = warnings
        self.explicit = explicit
        self.path = path
        self.env = env

    def get(self, section: str, key: str) -> Any:
        return self.values[section][key]

    def get_str(self, section: str, key: str) -> str:
        value = self.values[section][key]
        assert isinstance(value, str)
        return value

    def get_int(self, section: str, key: str) -> int:
        value = self.values[section][key]
        assert isinstance(value, int)
        return value

    def get_float(self, section: str, key: str) -> float:
        value = self.values[section][key]
        assert isinstance(value, (int, float))
        return float(value)

    def get_bool(self, section: str, key: str) -> bool:
        value = self.values[section][key]
        assert isinstance(value, bool)
        return value

    def get_list(self, section: str, key: str) -> list[str]:
        value = self.values[section][key]
        assert isinstance(value, list)
        return [str(item) for item in value]

    def language_list(self, key: str) -> list[str]:
        if ("language", key) in self.explicit:
            return self.get_list("language", key)
        return list(PRESETS[self.get_str("language", "preset")][key])

    @property
    def vault(self) -> str:
        return paths.vault_dir(self.env, self.get_str("paths", "vault"))

    @property
    def state(self) -> str:
        return paths.state_dir(self.env, self.get_str("paths", "state"))

    @property
    def claude_dir(self) -> str:
        return paths.claude_dir(self.env, self.get_str("paths", "claude_dir"))

    def repo_roots(self) -> list[str]:
        override = self.env.get("JUNO_REPO_ROOTS", "")
        raw = override.split(os.pathsep) if override else self.get_list("repos", "roots")
        roots = [paths.expand(item, self.env) for item in raw if item]
        return [root for root in roots if os.path.isdir(root)]

    def overview_roots(self) -> list[str]:
        configured = [
            paths.expand(item, self.env) for item in self.get_list("repos", "overview_roots")
        ]
        if configured:
            return configured
        roots = self.repo_roots()
        return roots[:1]


def merge_values(
    data: Mapping[str, Any], warnings: list[str]
) -> tuple[dict[str, dict[str, Any]], set[tuple[str, str]]]:
    values = copy.deepcopy(DEFAULTS)
    explicit: set[tuple[str, str]] = set()
    for section, defaults in DEFAULTS.items():
        given = data.get(section)
        if given is None:
            continue
        if not isinstance(given, dict):
            warnings.append(f"config: [{section}] is not a table; defaults used")
            continue
        for key, default in defaults.items():
            if key not in given:
                continue
            if type_ok(default, given[key]):
                values[section][key] = given[key]
                explicit.add((section, key))
            else:
                warnings.append(f"config: {section}.{key} has the wrong type; default used")
    if values["language"]["preset"] not in PRESETS:
        warnings.append("config: language.preset is unknown; 'none' used")
        values["language"]["preset"] = "none"
    return values, explicit


def load(env: Mapping[str, str] | None = None, path: str | None = None) -> Config:
    env = os.environ if env is None else env
    path = path or paths.config_path(env)
    warnings: list[str] = []
    data: Mapping[str, Any] = {}
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        pass
    except (OSError, tomllib.TOMLDecodeError) as error:
        warnings.append(f"config: cannot read {path} ({error}); defaults used")
    values, explicit = merge_values(data, warnings)
    return Config(values, warnings, explicit, path, env)
