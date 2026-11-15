from typing import Any

from juno import config
from juno.adapters import store
from juno.core import privacyack


def read(cfg: config.Config) -> list[dict[str, Any]]:
    path = store.vault_file(cfg, privacyack.ACK_FILE)
    return privacyack.parse(store.read_text(path)) if path else []


def hashes(cfg: config.Config) -> frozenset[str]:
    return privacyack.hashes(read(cfg))
