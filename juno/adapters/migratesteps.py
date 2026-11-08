import os

from juno.adapters import fs
from juno.core import migrate


def stale_exception(vault: str) -> None:
    path = os.path.join(vault, "core.md")
    raw = fs.read_bytes(path)
    if raw is None:
        return
    text = raw.decode("utf-8")
    updated = migrate.add_stale_exception(text)
    if updated != text:
        fs.atomic_write(path, updated.encode("utf-8"))


STEPS: dict[int, migrate.Step] = {1: stale_exception}
