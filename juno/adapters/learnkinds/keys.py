from typing import Any

from juno.adapters import cardwrite, keysio, store
from juno.adapters.learnkinds.base import Ctx
from juno.core import keys as keycore
from juno.core import search, vaultfmt

KEY = "keys"
SHAPE = list
LOCKED = False
STATE_RANK = 4


def instructions(ctx: Ctx) -> list[str]:
    return [
        '- "keys": 2-5 strings of 10-160 chars, or one context sentence of at most 240 chars, '
        "about the card you just wrote: the facts, names and terms someone would later type to "
        "find it, including wording the card itself does not use. Only what the digest shows. "
        "No `·`, no secrets, emails, phone numbers or ids of 6 or more digits."
    ]


def state(ctx: Ctx) -> str:
    return ""


def card_passages(ctx: Ctx) -> list[tuple[str, str]]:
    seg8 = ctx.seg.sid[:8]
    found: list[tuple[str, str]] = []
    for rel in ctx.card_files:
        path = cardwrite.safe_path(ctx.cfg, *rel.split("/"))
        if path is None:
            continue
        _, body = vaultfmt.parse_frontmatter(store.read_text(path))
        for head, raw in search.split_passages(body):
            if any(part.endswith(f" · {seg8}") for part in head.split(" > ")):
                found.append((rel, raw))
    return found


def apply(ctx: Ctx, data: dict[str, Any], sections: dict[str, str]) -> None:
    if not data.get(KEY):
        return
    facts, reason, dropped = keycore.check_facts(data[KEY])
    if facts is None:
        sections[KEY] = f"dropped:{reason}"
        return
    model = ctx.cfg.get_str("keys", "model") or ctx.cfg.get_str("handoff", "model")
    rows = {
        keycore.passage_hash(raw): keysio.row(
            keycore.passage_hash(raw), rel, facts, "card", model, dropped=dropped
        )
        for rel, raw in card_passages(ctx)
    }
    if not rows:
        sections[KEY] = "dropped:no card passage"
        return
    keysio.append(ctx.cfg.state, rows.values())
    ctx.result[KEY] = {"stored": len(rows)}
    sections[KEY] = "ok"
