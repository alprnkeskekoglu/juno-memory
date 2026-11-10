import os

from juno import config, paths
from juno.adapters import fs, store
from juno.core import protocolsync


def template_dir() -> str:
    return os.path.join(paths.template_dir(), "protocol")


def vault_path(cfg: config.Config, name: str) -> str:
    return os.path.join(cfg.vault, "protocol", name)


def read(path: str) -> str | None:
    raw = fs.read_bytes(path)
    return None if raw is None else raw.decode("utf-8", "replace")


def scan(cfg: config.Config) -> list[tuple[str, str]]:
    """(file name, state) for each shipped protocol file; state per protocolsync.classify."""
    found = []
    directory = template_dir()
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        path = vault_path(cfg, name)
        wanted = read(os.path.join(directory, name)) or ""
        if os.path.islink(path) or os.path.islink(os.path.dirname(path)):
            state = "modified"
        else:
            state = protocolsync.classify(name, wanted, read(path))
        found.append((name, state))
    return found


def update(cfg: config.Config, name: str) -> None:
    """Replace an outdated vault protocol file with the template (backup first)."""
    path = vault_path(cfg, name)
    store.backup(cfg, path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fs.atomic_write(path, (fs.read_bytes(os.path.join(template_dir(), name)) or b""), 0o644)
