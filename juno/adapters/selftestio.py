"""State file of `juno selftest`: `selftest.json` in the state dir (mode 0600, atomic, locked)."""

import contextlib
import json
import os
from collections.abc import Iterator
from typing import Any

from juno import config
from juno.adapters import fs, index, indexquery
from juno.core import selftest as core

STATE_FILE = "selftest.json"
LOCK_FILE = "selftest.lock"
LOCK_SECONDS = 5.0


def state_path(cfg: config.Config) -> str:
    return os.path.join(cfg.state, STATE_FILE)


def load(cfg: config.Config) -> dict[str, Any]:
    try:
        data = json.loads((fs.read_bytes(state_path(cfg)) or b"{}").decode("utf-8", "replace"))
    except ValueError:
        data = {}
    rows = data.get("questions") if isinstance(data, dict) else None
    good = [r for r in rows or [] if isinstance(r, dict) and isinstance(r.get("id"), str)]
    nxt = data.get("next") if isinstance(data, dict) else None
    return {"next": nxt if isinstance(nxt, int) else len(good) + 1, "questions": good}


def save(cfg: config.Config, state: dict[str, Any]) -> None:
    fs.ensure_private_dir(cfg.state)
    text = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fs.atomic_write(state_path(cfg), text.encode(), 0o600)


@contextlib.contextmanager
def locked(cfg: config.Config) -> Iterator[None]:
    fs.ensure_private_dir(cfg.state)
    with fs.locked(os.path.join(cfg.state, LOCK_FILE), LOCK_SECONDS):
        yield


def vault_file(cfg: config.Config, rel: str) -> None:
    """Raise Invalid unless `rel` is a regular file in the vault with no symlink on the way."""
    path = os.path.abspath(cfg.vault)
    for part in rel.split("/"):
        path = os.path.join(path, part)
        if os.path.islink(path):
            raise core.Invalid("--expect goes through a symlink")
    if not os.path.isfile(path):
        raise core.Invalid(f"--expect: no such file in the vault: {rel}")


def search(
    cfg: config.Config, db: str, query: str, limit: int, corr: dict[str, list[str]] | None = None
) -> list[dict[str, Any]]:
    """The retrieval `juno search <query> --limit K` runs, with no filters."""
    return indexquery.search(
        db,
        query,
        repo=None,
        ticket=None,
        limit=limit,
        corrections={} if corr is None else corr,
        spread=True,
        archive=False,
        preset=cfg.get_str("language", "preset"),
        ticket_files=bool(cfg.get_list("jira", "prefixes")),
        keys_weight=cfg.get_float("keys", "weight"),
        since=None,
        until=None,
    )


def synced_db(cfg: config.Config) -> str:
    return index.sync(cfg)[0]
