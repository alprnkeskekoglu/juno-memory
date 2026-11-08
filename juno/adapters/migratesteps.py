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


def _replace_in(vault: str, rel: str, old: str, new: str) -> None:
    path = os.path.join(vault, rel)
    raw = fs.read_bytes(path)
    if raw is None:
        return
    text = raw.decode("utf-8")
    updated = migrate.replace_exact(text, old, new)
    if updated != text:
        fs.atomic_write(path, updated.encode("utf-8"))


def quote_and_cap_text(vault: str) -> None:
    _replace_in(
        vault,
        os.path.join("protocol", "rules.md"),
        migrate.RULES_QUOTE_OLD,
        migrate.RULES_QUOTE_NEW,
    )
    _replace_in(vault, "core.md", migrate.CAPS_OLD, migrate.CAPS_NEW)


STEPS: dict[int, migrate.Step] = {1: stale_exception, 2: quote_and_cap_text}
