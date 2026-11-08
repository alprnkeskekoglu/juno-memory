import contextlib
import time
from typing import Any

from juno import config
from juno.adapters import cardwrite, learnkinds, store, usageio
from juno.adapters.learnkinds.base import STATE_TAIL, Ctx
from juno.core import extract, extract_prompt
from juno.core.ledger import Segment


def enabled(cfg: config.Config) -> bool:
    return cfg.get_bool("handoff", "extract")


def instructions(
    cfg: config.Config,
    seg: Segment,
    digest: str | None = None,
    turns: tuple[list[tuple[int, str]], int] | None = None,
) -> list[str]:
    ctx = Ctx(cfg, seg, digest, turns=turns[0] if turns else None)
    keys: list[str] = []
    bullets: list[str] = []
    for kind in learnkinds.KINDS:
        lines = kind.instructions(ctx)
        if lines:
            keys.append(kind.KEY)
            bullets += lines
    return extract_prompt.instructions(keys, bullets)


def vault_state(
    cfg: config.Config,
    seg: Segment,
    digest: str | None = None,
    turns: tuple[list[tuple[int, str]], int] | None = None,
) -> str:
    ctx = Ctx(
        cfg, seg, digest, turns=turns[0] if turns else None, turns_total=turns[1] if turns else 0
    )
    ranked = sorted(learnkinds.KINDS, key=lambda kind: kind.STATE_RANK)
    body = [kind.state(ctx) for kind in ranked if kind.STATE_RANK < STATE_TAIL]
    tail = "".join(kind.state(ctx) for kind in ranked if kind.STATE_RANK >= STATE_TAIL)
    return extract_prompt.vault_state([text for text in body if text], tail)


def guarded(cfg: config.Config, kind: str, sections: dict[str, str], call: Any) -> None:
    try:
        call()
    except TimeoutError:
        sections[kind] = "dropped:vault lock busy"
    except Exception as exc:
        store.log_error(cfg, f"card-learn.{kind}", exc)
        sections[kind] = f"dropped:error:{type(exc).__name__}"


def log_cites(cfg: config.Config, seg: Segment, data: dict[str, Any]) -> None:
    evidence = [
        item["evidence"]
        for items in data.values()
        if isinstance(items, list)
        for item in items
        if isinstance(item, dict) and isinstance(item.get("evidence"), str)
    ]
    with contextlib.suppress(OSError):
        usageio.append(cfg.state, seg.sid, usageio.cited_rels(cfg, evidence), "cite")


def apply(
    cfg: config.Config,
    seg: Segment,
    raw: str | None,
    digest: str,
    card_files: list[str],
    now: float | None = None,
    turns: tuple[list[tuple[int, str]], int] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    ctx = Ctx(
        cfg,
        seg,
        digest,
        card_files,
        now or time.time(),
        turns=turns[0] if turns else None,
        turns_total=turns[1] if turns else 0,
    )
    sections = dict.fromkeys(learnkinds.KEYS, "none")
    ctx.result["extract_error"] = None
    try:
        data = extract.parse_extract(raw)
    except ValueError as exc:
        ctx.result["extract_error"] = str(exc)
        return ctx.result, dict.fromkeys(learnkinds.KEYS, f"dropped:{exc}")
    log_cites(cfg, seg, data)
    for kind in learnkinds.KINDS:
        if kind.KEY in data and not isinstance(data[kind.KEY], kind.SHAPE):
            sections[kind.KEY] = "dropped:invalid"
    for kind in learnkinds.KINDS:
        if not kind.LOCKED:
            guarded(cfg, kind.KEY, sections, lambda kind=kind: kind.apply(ctx, data, sections))
    with contextlib.ExitStack() as stack:
        locked = [kind for kind in learnkinds.KINDS if kind.LOCKED]
        try:
            stack.enter_context(cardwrite.vault_lock(cfg))
        except TimeoutError:
            for kind in locked:
                if data.get(kind.KEY) and sections[kind.KEY] == "none":
                    sections[kind.KEY] = "dropped:vault lock busy"
            return ctx.result, sections
        for kind in locked:
            guarded(cfg, kind.KEY, sections, lambda kind=kind: kind.apply(ctx, data, sections))
    return ctx.result, sections
