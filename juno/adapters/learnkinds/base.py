import os
import time
from dataclasses import dataclass, field
from typing import Any

from juno import config
from juno.adapters import store
from juno.core.ledger import Segment

STATE_TAIL = 9


@dataclass
class Ctx:
    cfg: config.Config
    seg: Segment
    digest: str | None = None
    card_files: list[str] = field(default_factory=list)
    now: float = 0.0
    result: dict[str, Any] = field(default_factory=dict)
    turns: list[tuple[int, str]] | None = None
    turns_total: int = 0


def today() -> str:
    return time.strftime("%Y-%m-%d")


def list_md(cfg: config.Config, sub: str) -> list[str]:
    try:
        return sorted(n for n in os.listdir(os.path.join(cfg.vault, sub)) if n.endswith(".md"))
    except OSError:
        return []


def vault_text(cfg: config.Config, rel: str) -> str:
    return store.read_text(os.path.join(cfg.vault, *rel.split("/")))


def status_of(proposed: int, done: int, reasons: dict[str, int]) -> str:
    if not proposed:
        return "none"
    if done:
        return "ok"
    top = max(reasons, key=lambda k: reasons[k]) if reasons else "invalid"
    return f"dropped:{top}"
