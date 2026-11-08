import argparse
import json
import os
import stat
import subprocess
import tomllib
from collections.abc import Mapping
from typing import Any

from juno import config
from juno.adapters import fs, interpreter, keysbackfill, ledgerio, store, vault
from juno.cmd import common, doctor_vault, init, write
from juno.core import interp, migrate, settings_merge, shim, tomlio

Check = tuple[str, str, str]
KEYS_COVERAGE = 0.8


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    parser.add_argument(
        "--purge-synthetic",
        action="store_true",
        help="remove harness-generated prompt records from the segment ledgers",
    )
    common.add_install_options(parser)


def check_interpreter(cfg: config.Config) -> list[Check]:
    python = cfg.get_str("runtime", "python")
    if not python:
        return [("FAIL", "interpreter", "no interpreter cached; run juno init")]
    version = interpreter.probe(python)
    if version is None or version < interp.FLOOR:
        return [("FAIL", "interpreter", interp.refusal(python, version))]
    out: list[Check] = [("PASS", "interpreter", f"{python} is {version[0]}.{version[1]}")]
    raw = fs.read_bytes(cfg.path) or b""
    text = raw.decode("utf-8", "replace")
    if tomlio.read_key(text, "python") != python:
        out.append(
            ("FAIL", "readers", "the launcher's sed and tomllib read different interpreters")
        )
    elif not tomlio.runtime_is_first_table(text):
        out.append(("WARN", "readers", "[runtime] is not the first table in the config"))
    else:
        out.append(("PASS", "readers", "launcher and config readers agree"))
    return out


def check_retired_keys(cfg: config.Config) -> list[Check]:
    try:
        data = tomllib.loads((fs.read_bytes(cfg.path) or b"").decode("utf-8", "replace"))
    except tomllib.TOMLDecodeError:
        return []
    section = data.get("ab")
    if isinstance(section, dict) and "card_holdout" in section:
        return [("WARN", "card_holdout", "[ab] card_holdout is ignored; use juno arm")]
    return []


def check_keys(cfg: config.Config) -> list[Check]:
    if cfg.get_float("keys", "weight") <= 0:
        return []
    keyed, total = keysbackfill.coverage(cfg)
    if total and keyed / total < KEYS_COVERAGE:
        pct = round(100 * keyed / total)
        return [("WARN", "keys", f"{pct}% of passages have fact keys; run juno keys backfill")]
    return []


def check_stale(cfg: config.Config) -> list[Check]:
    if not cfg.get_bool("stale", "auto_fix"):
        return []
    core = store.read_text(os.path.join(cfg.vault, "core.md"))
    if migrate.privacy_has_exception(core):
        return []
    return [
        (
            "WARN",
            "stale",
            "[stale] auto_fix is on but core.md Privacy has no stale fixer exception; "
            "run juno migrate or add the sentence by hand",
        )
    ]


def check_paths(cfg: config.Config) -> list[Check]:
    out: list[Check] = []
    if cfg.warnings:
        out += [("WARN", "config", w) for w in cfg.warnings]
    else:
        out.append(
            ("PASS", "config", cfg.path if os.path.exists(cfg.path) else "defaults (no file)")
        )
    out += check_retired_keys(cfg)
    state = cfg.state
    if not os.path.isdir(state):
        out.append(("WARN", "state", f"{state} missing; run juno init"))
    elif stat.S_IMODE(os.stat(state).st_mode) & 0o077:
        out.append(("WARN", "state", f"{state} is not mode 0700"))
    else:
        out.append(("PASS", "state", state))
    state_of_vault = vault.status(cfg.vault)
    if state_of_vault != "juno":
        out.append(("FAIL", "vault", f"{cfg.vault} is {state_of_vault}; run juno init"))
    else:
        found = vault.schema_version(cfg.vault)
        if found == migrate.CODE_SCHEMA_VERSION:
            out.append(("PASS", "vault", f"{cfg.vault} (schema {found})"))
        else:
            out.append(
                ("FAIL", "vault", f"schema {found}, code expects {migrate.CODE_SCHEMA_VERSION}")
            )
    cap = cfg.get_int("caps", "hook_output_chars")
    out.append(
        ("PASS", "hook cap", str(cap))
        if cap < 10000
        else ("FAIL", "hook cap", f"hook_output_chars {cap} must stay below 10000")
    )
    return out


def check_plugin(cfg: config.Config, settings: str) -> list[Check]:
    root = cfg.get_str("runtime", "plugin_root")
    if not root or not os.path.isfile(os.path.join(root, "juno", "cli.py")):
        return [("FAIL", "plugin root", f"{root or '(unset)'} has no juno/cli.py; run juno init")]
    status = init.plugin_status(settings).removeprefix("plugin: ")
    level = "PASS" if status.endswith("is installed") else "WARN"
    return [("PASS", "plugin root", root), (level, "plugin", status)]


def check_shim(args: argparse.Namespace, cfg: config.Config) -> list[Check]:
    target = os.path.join(common.bin_dir(args, cfg), "juno")
    raw = fs.read_bytes(target)
    if raw is None:
        return [("WARN", "shim", f"{target} missing; run juno init")]
    if shim.MARKER not in raw.decode("utf-8", "replace"):
        return [("WARN", "shim", f"{target} is not Juno's")]
    try:
        done = subprocess.run(
            [target, "version"], capture_output=True, text=True, timeout=10, env=dict(cfg.env)
        )
    except (OSError, subprocess.SubprocessError) as error:
        return [("FAIL", "shim", f"{target} cannot run ({error})")]
    if done.returncode != 0:
        return [("FAIL", "shim", f"{target} exits {done.returncode}: {done.stderr.strip()}")]
    return [("PASS", "shim", f"{target} resolves ({done.stdout.strip()})")]


def check_settings(cfg: config.Config, settings: str) -> list[Check]:
    raw = fs.read_bytes(settings)
    if raw is None:
        return [("WARN", "settings", f"{settings} missing")]
    try:
        data = settings_merge.parse(raw)
        manifest = common.load_manifest(cfg)
    except (settings_merge.SettingsError, ValueError) as error:
        return [("FAIL", "settings", str(error))]
    out: list[Check] = []
    if manifest is None or "record" not in manifest:
        out.append(("WARN", "settings merge", "no install manifest; run juno init"))
    else:
        perms = data.get("permissions")
        perms = perms if isinstance(perms, dict) else {}
        missing = [
            e
            for key, entries in manifest["record"].get("added", {}).items()
            for e in entries
            if e not in (perms.get(key) or [])
        ]
        out.append(
            ("FAIL", "settings merge", f"missing entries: {', '.join(missing)}")
            if missing
            else ("PASS", "settings merge", "recorded entries present")
        )
    out.append(
        ("PASS", "autoMemoryEnabled", "false")
        if data.get("autoMemoryEnabled") is False
        else ("WARN", "autoMemoryEnabled", "not false; native memory may return")
    )
    return out


def collect(args: argparse.Namespace, cfg: config.Config) -> list[Check]:
    first = check_interpreter(cfg)
    if first[0][0] == "FAIL" and "needs Python" in first[0][2]:
        return first[:1]
    settings = common.settings_path(args, cfg)
    out = (
        first
        + check_paths(cfg)
        + check_plugin(cfg, settings)
        + check_shim(args, cfg)
        + check_settings(cfg, settings)
    )
    if vault.status(cfg.vault) == "juno":
        out += doctor_vault.collect(cfg, run_probe=not args.json) + check_keys(cfg)
        out += check_stale(cfg)
    return out


def purge(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    def body(cfg: config.Config) -> int:
        write.guard(cfg)
        done = ledgerio.purge_synthetic(cfg)
        print(
            f"Juno: purged {done['records']} harness-generated prompt records "
            f"({done['prompts']} prompts) from {done['ledgers']} ledgers; originals saved "
            f"under {cfg.state}/backups."
        )
        busy = [str(b) for b in done["busy"]] if isinstance(done["busy"], list) else []
        if busy:
            print("Juno: skipped ledgers still being written: " + ", ".join(busy))
            return 1
        return 0

    return write.execute(args, env, body)


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    if args.purge_synthetic:
        return purge(args, env)
    cfg = config.load(env)
    checks = collect(args, cfg)
    failed = any(level == "FAIL" for level, _, _ in checks)
    if args.json:
        payload: dict[str, Any] = {
            "ok": not failed,
            "checks": [{"status": s, "name": n, "detail": d} for s, n, d in checks],
        }
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for level, name, detail in checks:
            print(f"{level} {name}: {detail}")
    return 1 if failed else 0
