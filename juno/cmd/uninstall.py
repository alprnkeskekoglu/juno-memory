import argparse
import os
from collections.abc import Mapping
from typing import Any

from juno import config
from juno.adapters import fs
from juno.cmd import common
from juno.cmd.common import say
from juno.core import settings_merge, shim


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run", action="store_true", help="show what would change, write nothing"
    )
    common.add_install_options(parser)


def restore_settings(path: str, info: dict[str, Any], record: dict[str, Any], dry: bool) -> None:
    raw = fs.read_bytes(path)
    if raw is None:
        say(f"settings: {path} is gone; nothing to restore")
        return
    if fs.sha256(raw) == info.get("post_sha256"):
        original = fs.read_bytes(info.get("backup", ""))
        if original is not None and fs.sha256(original) == info.get("original_sha256"):
            if info.get("existed"):
                verb = "would restore" if dry else "restored"
                say(f"settings: {verb} the original bytes of {path}")
                if not dry:
                    fs.atomic_write(path, original)
            else:
                say(f"settings: {'would remove' if dry else 'removed'} {path} (Juno created it)")
                if not dry:
                    os.unlink(path)
            return
        say("settings: the backup is missing or changed; removing the recorded entries instead")
    data = settings_merge.parse(raw)
    new, notes = settings_merge.unmerge(data, record)
    for note in notes:
        say("settings: " + note)
    indent = settings_merge.detect_indent(raw)
    new_bytes = settings_merge.dumps(new, indent, raw.endswith(b"\n"))
    say(f"settings: {'would remove' if dry else 'removed'} the recorded entries from {path}")
    if not dry:
        fs.atomic_write(path, new_bytes)


def remove_shim(info: dict[str, Any], dry: bool) -> None:
    path = info.get("path", "")
    raw = fs.read_bytes(path) if path else None
    if raw is None:
        say(f"shim: {path or '(none recorded)'} not present")
    elif shim.MARKER not in raw.decode("utf-8", "replace"):
        say(f"shim: {path} is not Juno's; left alone")
    else:
        say(f"shim: {'would remove' if dry else 'removed'} {path}")
        if not dry:
            os.unlink(path)


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    dry = bool(args.dry_run)
    cfg = config.load(env)
    try:
        manifest = common.load_manifest(cfg)
    except ValueError as error:
        say(f"Juno: {error}")
        return 2
    if manifest is None:
        say("nothing to uninstall: no install manifest")
        return 0
    if "settings" in manifest:
        chosen = getattr(args, "claude_settings", None)
        path = common.settings_path(args, cfg) if chosen else manifest["settings"]["path"]
        try:
            restore_settings(path, manifest["settings"], manifest.get("record", {}), dry)
        except settings_merge.SettingsError as error:
            say(f"Juno: {error}")
            return 2
    if "shim" in manifest:
        remove_shim(manifest["shim"], dry)
    if not dry:
        os.unlink(common.manifest_path(cfg))
    say("vault: untouched")
    return 0
