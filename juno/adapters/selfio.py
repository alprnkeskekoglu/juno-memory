from typing import Any

from juno import config
from juno.adapters import asklog, cardjob, store
from juno.core import selfmodel


def turns_of(rows: list[dict[str, Any]]) -> tuple[int, int]:
    found = [r["turns"] for r in rows if isinstance(r.get("turns"), dict)]
    return (
        max((int(t.get("proposals") or 0) for t in found), default=0),
        max((int(t.get("pushback") or 0) for t in found), default=0),
    )


def counters(cfg: config.Config, sid: str, name: str) -> dict[str, Any]:
    asks = [r for r in asklog.read(cfg.state) if r["sid"] == sid]
    single = [r for r in asks if selfmodel.is_unit(r)]
    proposals, pushback = turns_of([r for r in cardjob.read_runs(cfg) if r.get("sid") == sid])
    return {
        "asks": len(single),
        "rec": sum(1 for r in single if selfmodel.took_rec(r)),
        "proposals": proposals,
        "pushback": pushback,
    }


def segment_asks(cfg: config.Config, sid: str) -> list[dict[str, Any]]:
    return [r for r in asklog.read(cfg.state) if r["sid"] == sid]


def texts(cfg: config.Config) -> list[str]:
    return [store.read_text(store.vault_file(cfg, f"self/{n}.md") or "") for n in selfmodel.FILES]


def model_block(cfg: config.Config) -> str:
    return selfmodel.block(
        texts(cfg), cfg.get_int("self", "max_lines"), cfg.get_int("self", "max_chars")
    )
