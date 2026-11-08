import argparse
import os
import shutil
import time
from collections.abc import Mapping

from juno import config
from juno.adapters import fs, migratesteps, vault
from juno.cmd.common import say
from juno.core import migrate


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="list the steps, change nothing")


def run(
    args: argparse.Namespace,
    env: Mapping[str, str],
    steps: Mapping[int, migrate.Step] | None = None,
    target: int | None = None,
) -> int:
    steps = migratesteps.STEPS if steps is None else steps
    target = migrate.CODE_SCHEMA_VERSION if target is None else target
    cfg = config.load(env)
    if vault.status(cfg.vault) != "juno":
        say(f"Juno: no Juno vault at {cfg.vault}; run juno init")
        return 2
    current = vault.schema_version(cfg.vault)
    if current is None:
        say("Juno: juno-brain.toml has no schema_version")
        return 2
    if current > target:
        say(f"Juno: vault schema {current} is newer than this Juno ({target}); update Juno")
        return 2
    try:
        wanted = migrate.plan(current, target, steps)
    except ValueError as error:
        say(f"Juno: {error}")
        return 2
    if not wanted:
        say(f"migrate: vault is at schema {current}; nothing to do")
        return 0
    toml_path = os.path.join(cfg.vault, vault.TOML_NAME)
    for version in wanted:
        say(f"migrate: schema {version} -> {version + 1}" + (" (dry run)" if args.dry_run else ""))
        if args.dry_run:
            continue
        stamp = time.strftime("%Y%m%dT%H%M%S")
        backup = os.path.join(cfg.state, "backups", f"migrate-{stamp}-v{version}")
        fs.ensure_private_dir(os.path.dirname(backup))
        shutil.copytree(cfg.vault, backup, symlinks=True)
        steps[version](cfg.vault)
        text = (fs.read_bytes(toml_path) or b"").decode("utf-8")
        fs.atomic_write(toml_path, migrate.write_schema(text, version + 1).encode("utf-8"))
        say(f"migrate: now at schema {version + 1} (backup {backup})")
    return 0
