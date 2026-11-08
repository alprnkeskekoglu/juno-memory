import os
import shutil
import subprocess

from juno.adapters import fs
from juno.core import migrate

TOML_NAME = "juno-brain.toml"
CREATED_TOKEN = "__CREATED__"


def schema_version(vault: str) -> int | None:
    raw = fs.read_bytes(os.path.join(vault, TOML_NAME))
    return None if raw is None else migrate.read_schema(raw.decode("utf-8", "replace"))


def status(vault: str) -> str:
    if not os.path.lexists(vault):
        return "missing"
    if not os.path.isdir(vault):
        return "foreign"
    if os.path.isfile(os.path.join(vault, TOML_NAME)):
        return "juno"
    return "empty" if not os.listdir(vault) else "foreign"


def populate(
    vault: str, template: str, created: str, skip: frozenset[str] = frozenset()
) -> list[str]:
    written: list[str] = []
    for root, dirs, files in os.walk(template):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        rel = os.path.relpath(root, template)
        target_dir = vault if rel == "." else os.path.join(vault, rel)
        os.makedirs(target_dir, exist_ok=True)
        for name in files:
            target = os.path.join(target_dir, name)
            if (name in skip and rel == ".") or os.path.lexists(target):
                continue
            source = os.path.join(root, name)
            if name == TOML_NAME:
                with open(source, encoding="utf-8") as handle:
                    text = handle.read().replace(CREATED_TOKEN, created)
                fs.atomic_write(target, text.encode("utf-8"), 0o644)
            else:
                shutil.copyfile(source, target)
            written.append(os.path.relpath(target, vault))
    return written


def git_init(vault: str) -> bool:
    if os.path.exists(os.path.join(vault, ".git")):
        return True
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        done = subprocess.run(
            ["git", "init", "-q", vault],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    except OSError, subprocess.SubprocessError:
        return False
    return done.returncode == 0
