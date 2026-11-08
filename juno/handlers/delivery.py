import dataclasses
from typing import Any

from juno import config
from juno.adapters import armsio, ledgerio, nextio
from juno.core import packs
from juno.core.ledger import Segment
from juno.core.packs import Caps, Card, NextPlan, Wanted
from juno.core.view import VaultView


def deliver_packs(
    cfg: config.Config, view: VaultView, seg: Segment, wanted: Wanted, budget: int
) -> str:
    caps = Caps.from_config(cfg)
    arms = armsio.arms_for(cfg, seg.sid)
    side = arms.get("linked")
    if side:
        caps = dataclasses.replace(caps, linked=side == "on")
    next_side = arms.get("next")
    stored = nextio.load(cfg) if next_side else {}
    parts: list[str] = []
    used = 0
    cards: list[Card] | None = None
    shown: set[str] = set()
    for kind, key in wanted:
        pack_key = f"{kind}:{key}"
        if pack_key in seg.packs:
            continue
        cards = packs.all_cards(view) if cards is None else cards
        plan = (
            NextPlan({k: nextio.block(v) for k, v in stored.items()}, next_side == "on")
            if next_side
            else None
        )
        text = packs.render_pack(view, caps, kind, key, cards, shown, plan)
        if not text:
            continue
        mode = "full"
        if seg.full_packs >= caps.max_full_packs or used + len(text) + 2 > budget:
            text, mode = packs.pack_pointer(view, kind, key), "pointer"
            if used + len(text) + 2 > budget:
                continue
        parts.append(text)
        used += len(text) + 2
        event: dict[str, Any] = {"ev": "pack", "key": pack_key, "mode": mode}
        nb = view.linked_paths(kind, key, packs.own_files(kind, key))
        if nb:
            event["nb"] = nb
        if mode == "full" and plan and plan.seg:
            held = not plan.show or f"[Next · {plan.seg}]" not in text
            event["next_held" if held else "next_shown"] = plan.seg
        ledgerio.record(cfg, seg, event)
    return "\n\n".join(parts)
