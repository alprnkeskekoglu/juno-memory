import argparse
import datetime
import difflib
import json
import os
import sys
import time
from collections.abc import Mapping
from typing import Any

from juno import config, paths
from juno.adapters import fs, interpreter, schedule, vault
from juno.cmd import common
from juno.cmd.common import say
from juno.core import interp, migrate, settings_merge, shim, tomlio
from juno.core.errors import Refused


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run", action="store_true", help="show what would change, write nothing"
    )
    common.add_install_options(parser)


def init_config(cfg: config.Config, py: str, root: str, dry: bool) -> bool:
    raw = fs.read_bytes(cfg.path)
    text = raw.decode("utf-8") if raw is not None else ""
    updated = tomlio.set_runtime(text, py, root)
    if updated == text:
        say(f"config: {cfg.path} already current")
        return False
    say(f"config: {'would write' if dry else 'wrote'} [runtime] to {cfg.path}")
    if not dry:
        fs.atomic_write(cfg.path, updated.encode("utf-8"), 0o600)
    return True


def init_vault(cfg: config.Config, dry: bool) -> str | None:
    path = cfg.vault
    state = vault.status(path)
    if state == "foreign":
        return f"vault: {path} exists and has no {vault.TOML_NAME}; refusing to touch it"
    if state == "juno":
        found = vault.schema_version(path)
        code = migrate.CODE_SCHEMA_VERSION
        if found is None or found < migrate.CODE_SCHEMA_VERSION:
            return f"vault: schema {found} is older than {code}; run juno migrate"
        if found > migrate.CODE_SCHEMA_VERSION:
            return f"vault: schema {found} is newer than this Juno ({code}); update Juno"
        say(f"vault: {path} exists (schema {found}); left untouched")
        return None
    if dry:
        say(f"vault: would create {path} from the template")
        return None
    created = datetime.date.today().isoformat()
    vault.populate(path, paths.template_dir(), created)
    git_ok = vault.git_init(path)
    say(f"vault: created {path}" + ("" if git_ok else " (git init failed; run it by hand)"))
    return None


def init_settings(
    cfg: config.Config, settings: str, manifest: dict[str, Any] | None, dry: bool
) -> dict[str, Any]:
    raw = fs.read_bytes(settings)
    data = settings_merge.parse(raw or b"")
    if manifest and manifest.get("settings", {}).get("path") != settings:
        raise ValueError(
            "the install manifest records another settings file; run juno uninstall first"
        )
    block = settings_merge.build_block(cfg.vault, paths.home(cfg.env))
    new, record = settings_merge.merge(data, block)
    if new == data:
        say(f"settings: {settings} already merged")
        return manifest or {}
    indent = settings_merge.detect_indent(raw or b"")
    newline = raw.endswith(b"\n") if raw else True
    new_bytes = settings_merge.dumps(new, indent, newline)
    if dry:
        diff = difflib.unified_diff(
            (raw or b"").decode("utf-8").splitlines(),
            new_bytes.decode("utf-8").splitlines(),
            "settings (before)",
            "settings (after)",
            lineterm="",
        )
        say("settings: would merge:")
        for line in diff:
            say("  " + line)
        return manifest or {}
    updated: dict[str, Any] = dict(manifest) if manifest else {"version": common.MANIFEST_VERSION}
    if manifest and "settings" in manifest:
        record = settings_merge.merge_records(manifest["record"], record)
        info = dict(manifest["settings"])
    else:
        fs.ensure_private_dir(cfg.state)
        backups = os.path.join(cfg.state, "install-backups")
        fs.ensure_private_dir(backups)
        stamp = time.strftime("%Y%m%dT%H%M%S")
        backup = os.path.join(backups, f"settings-{stamp}-{fs.sha256(raw or b'')[:8]}.json")
        fs.atomic_write(backup, raw or b"", 0o600)
        info = {
            "path": settings,
            "existed": raw is not None,
            "original_sha256": fs.sha256(raw or b""),
            "backup": backup,
        }
    mode = None if raw is not None else 0o600
    fs.atomic_write(settings, new_bytes, mode)
    check = settings_merge.parse(fs.read_bytes(settings) or b"")
    if check != new:
        raise ValueError("settings verification failed after writing")
    info["post_sha256"] = fs.sha256(new_bytes)
    updated["settings"] = info
    updated["record"] = record
    common.save_manifest(cfg, updated)
    say(f"settings: merged into {settings} (backup {info['backup']})")
    return updated


def init_shim(
    cfg: config.Config,
    target_dir: str,
    manifest: dict[str, Any],
    dry: bool,
) -> str | None:
    target = os.path.join(target_dir, "juno")
    existing = fs.read_bytes(target)
    if existing is not None and shim.MARKER not in existing.decode("utf-8", "replace"):
        return f"shim: {target} exists and is not Juno's; refusing to replace it"
    if existing == shim.SHIM.encode("utf-8"):
        say(f"shim: {target} already current")
        return None
    if dry:
        say(f"shim: would write {target}")
        return None
    os.makedirs(target_dir, exist_ok=True)
    fs.atomic_write(target, shim.SHIM.encode("utf-8"), 0o755)
    previous = manifest.get("shim", {}) if manifest else {}
    created = previous.get("created", existing is None)
    manifest = dict(manifest) if manifest else {"version": common.MANIFEST_VERSION}
    manifest["shim"] = {"path": target, "created": created}
    common.save_manifest(cfg, manifest)
    say(f"shim: wrote {target}")
    return None


def plugin_status(settings: str) -> str:
    claude = os.path.dirname(settings)
    raw = fs.read_bytes(os.path.join(claude, "plugins", "installed_plugins.json"))
    installed = False
    if raw:
        try:
            data = json.loads(raw.decode("utf-8"))
            plugins = data.get("plugins", {}) if isinstance(data, dict) else {}
            installed = any(str(key).startswith("juno@") for key in plugins)
        except ValueError:
            pass
    if installed:
        return "plugin: juno is installed"
    root = paths.plugin_root()
    return (
        "plugin: not installed; run: "
        f'claude plugin marketplace add "{root}" && claude plugin install {common.PLUGIN_ID}'
    )


ASKED_MARKER = "stale-fix-asked"
STYLE_MARKER = "output-style-asked"
STYLE_CHOICES = {
    "1": "juno:juno",
    "2": "juno:juno-witty",
    "3": "juno:juno-warm",
    "4": "juno:juno-mentor",
}
OUTPUT_STYLES = tuple(STYLE_CHOICES.values())


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def offer_stale_fix(cfg: config.Config, env: Mapping[str, str]) -> None:
    marker = os.path.join(cfg.state, ASKED_MARKER)
    if not interactive() or ("stale", "auto_fix") in cfg.explicit or os.path.exists(marker):
        return
    budget = cfg.get_float("stale", "run_budget_usd")
    question = (
        "Fix stale notes automatically every Sunday? "
        f"(model calls, at most ${budget:g} a week) [y/N] "
    )
    try:
        answer = input(question).strip().lower()
    except EOFError:
        answer = ""
    fs.atomic_write(marker, b"asked\n", 0o600)
    if answer not in ("y", "yes"):
        say("stale fixer: left off; turn it on later with juno schedule install stale-weekly")
        return
    raw = fs.read_bytes(cfg.path)
    text = tomlio.set_table_bool((raw or b"").decode("utf-8"), "stale", "auto_fix", True)
    fs.atomic_write(cfg.path, text.encode("utf-8"), 0o600)
    say("config: [stale] auto_fix = true")
    try:
        for line in schedule.install(config.load(env)):
            say(line)
    except Refused as error:
        say(f"stale fixer: not scheduled ({error}); then run juno schedule install stale-weekly")


def offer_output_style(cfg: config.Config, settings: str) -> None:
    marker = os.path.join(cfg.state, STYLE_MARKER)
    if not interactive() or os.path.exists(marker):
        return
    raw = fs.read_bytes(settings)
    try:
        data = settings_merge.parse(raw or b"")
        manifest = common.load_manifest(cfg)
    except (ValueError, settings_merge.SettingsError) as error:
        say(f"output style: not set ({error})")
        return
    current = data.get("outputStyle")
    if current in OUTPUT_STYLES:
        say(f"output style: {current} already set")
        return
    if current is not None:
        say(f"output style: {current!r} is already set in {settings}; left as it is")
        return
    question = (
        "Juno style: 1) plain  2) witty  3) warm  4) mentor  5) none (Claude's default) [5]: "
    )
    try:
        answer = input(question).strip()
    except EOFError:
        answer = ""
    fs.atomic_write(marker, b"asked\n", 0o600)
    style = STYLE_CHOICES.get(answer)
    if style is None:
        say(f'output style: left off; turn it on later with "outputStyle": "{OUTPUT_STYLES[0]}"')
        return
    new = dict(data)
    new["outputStyle"] = style
    new_bytes = settings_merge.dumps(
        new, settings_merge.detect_indent(raw or b""), raw.endswith(b"\n") if raw else True
    )
    backups = os.path.join(cfg.state, "install-backups")
    fs.ensure_private_dir(backups)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    backup = os.path.join(backups, f"settings-{stamp}-{fs.sha256(raw or b'')[:8]}.json")
    fs.atomic_write(backup, raw or b"", 0o600)
    mode = None if raw is not None else 0o600
    fs.atomic_write(settings, new_bytes, mode)
    if settings_merge.parse(fs.read_bytes(settings) or b"") != new:
        say("output style: verification failed after writing; restore from " + backup)
        return
    if manifest and "settings" in manifest and "record" in manifest:
        manifest = dict(manifest)
        info = dict(manifest["settings"])
        if info.get("post_sha256") == fs.sha256(raw or b""):
            info["post_sha256"] = fs.sha256(new_bytes)
        manifest["settings"] = info
        manifest["record"] = {**manifest["record"], "outputStyle": style}
        common.save_manifest(cfg, manifest)
    say(f"settings: outputStyle = {style} in {settings} (backup {backup})")
    say("output style: restart Claude Code for it to take effect")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    dry = bool(args.dry_run)
    cfg = config.load(env)
    found = interpreter.discover(env)
    if found is None:
        floor = f"{interp.FLOOR[0]}.{interp.FLOOR[1]}"
        say(f"Juno needs Python >= {floor}; none found (set JUNO_PYTHON)")
        return 2
    py, version = found
    say(f"interpreter: {py} ({version[0]}.{version[1]})")
    try:
        init_config(cfg, py, paths.plugin_root(), dry)
    except ValueError as error:
        say(f"Juno: {error}")
        return 2
    cfg = config.load(env)
    if dry:
        say(f"state: would use {cfg.state}")
    else:
        fs.ensure_private_dir(cfg.state)
        say(f"state: {cfg.state}")
    problem = init_vault(cfg, dry)
    if problem:
        say("Juno: " + problem)
        return 2
    settings = common.settings_path(args, cfg)
    try:
        manifest = common.load_manifest(cfg)
        manifest = init_settings(cfg, settings, manifest, dry)
    except (ValueError, settings_merge.SettingsError) as error:
        say(f"Juno: {error}")
        return 2
    refused = init_shim(cfg, common.bin_dir(args, cfg), manifest, dry)
    if refused:
        say("Juno: " + refused)
    say(plugin_status(settings))
    if not dry and not refused:
        offer_output_style(config.load(env), settings)
        offer_stale_fix(config.load(env), env)
    return 2 if refused else 0
