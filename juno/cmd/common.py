import argparse
import json
import os
from collections.abc import Mapping
from typing import Any

from juno import config, paths
from juno.adapters import fs

MANIFEST_VERSION = 1
MARKETPLACE = "juno-local"
PLUGIN_ID = f"juno@{MARKETPLACE}"


def say(message: str) -> None:
    print(message)


def add_install_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--claude-settings",
        metavar="PATH",
        help="Claude settings file to merge into (default: <claude_dir>/settings.json)",
    )
    parser.add_argument(
        "--bin-dir",
        metavar="DIR",
        help="directory for the juno shim (default: JUNO_BIN_DIR or ~/.local/bin)",
    )


def settings_path(args: argparse.Namespace, cfg: config.Config) -> str:
    chosen = getattr(args, "claude_settings", None)
    if chosen:
        return paths.expand(chosen, cfg.env)
    return os.path.join(cfg.claude_dir, "settings.json")


def bin_dir(args: argparse.Namespace, cfg: config.Config) -> str:
    chosen = getattr(args, "bin_dir", None) or cfg.env.get("JUNO_BIN_DIR") or "~/.local/bin"
    return paths.expand(chosen, cfg.env)


def manifest_path(cfg: config.Config) -> str:
    return os.path.join(cfg.state, "install-manifest.json")


def load_manifest(cfg: config.Config) -> dict[str, Any] | None:
    raw = fs.read_bytes(manifest_path(cfg))
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError(f"cannot read {manifest_path(cfg)}: {error}") from error
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        raise ValueError(f"{manifest_path(cfg)} has an unknown format")
    return data


def save_manifest(cfg: config.Config, manifest: Mapping[str, Any]) -> None:
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    fs.ensure_private_dir(cfg.state)
    fs.atomic_write(manifest_path(cfg), text.encode("utf-8"), 0o600)
