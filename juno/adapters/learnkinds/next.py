from typing import Any

from juno.adapters import armsio, gate, nextio
from juno.adapters.learnkinds.base import Ctx
from juno.core.errors import Refused
from juno.core.textchecks import as_list, text_field

KEY = "next"
SHAPE = list
LOCKED = False
STATE_RANK = 6
MIN_PAIRS, MAX_PAIRS = 2, 3
Q_CHARS, A_CHARS = 120, 200


def instructions(ctx: Ctx) -> list[str]:
    if "next" not in armsio.running(ctx.cfg):
        return []
    return [
        f'- "next": {MIN_PAIRS}-{MAX_PAIRS} {{"q", "a"}}: questions the user is most likely to '
        "ask when this work resumes, each with a short answer, using only facts in the digest. "
        f'"q": at most {Q_CHARS} chars, "a": at most {A_CHARS} chars. No secrets, emails, phone '
        "numbers or ids of 6 or more digits."
    ]


def state(ctx: Ctx) -> str:
    return ""


def check(ctx: Ctx, items: list[object]) -> tuple[list[dict[str, str]] | None, str]:
    if not MIN_PAIRS <= len(items) <= MAX_PAIRS:
        return None, "invalid"
    pairs: list[dict[str, str]] = []
    for item in items:
        q = text_field(item.get("q")) if isinstance(item, dict) else ""
        a = text_field(item.get("a")) if isinstance(item, dict) else ""
        if not (0 < len(q) <= Q_CHARS and 0 < len(a) <= A_CHARS):
            return None, "invalid"
        try:
            pairs.append(
                {
                    "q": gate.scan_line(ctx.cfg, q, "question"),
                    "a": gate.scan_line(ctx.cfg, a, "answer"),
                }
            )
        except Refused:
            return None, "private"
    return pairs, ""


def apply(ctx: Ctx, data: dict[str, Any], sections: dict[str, str]) -> None:
    items = as_list(data.get(KEY))
    if not items or "next" not in armsio.running(ctx.cfg):
        return
    pairs, reason = check(ctx, items)
    if pairs is None:
        sections[KEY] = f"dropped:{reason}"
        return
    nextio.append(ctx.cfg, ctx.seg.sid[:8], ctx.card_files[0] if ctx.card_files else "", pairs)
    ctx.result[KEY] = {"stored": len(pairs)}
    sections[KEY] = "ok"
