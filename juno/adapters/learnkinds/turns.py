from typing import Any

from juno.adapters.learnkinds.base import Ctx

KEY = "turns"
SHAPE = dict
LOCKED = False
STATE_RANK = 9
MAX_COUNT = 500
NAMES = ("proposals", "pushback")


def instructions(ctx: Ctx) -> list[str]:
    if ctx.turns is not None:
        return [
            '- "turns": an object {"proposals": [i, ...], "pushback": [i, ...]} of indexes taken '
            'from the numbered assistant turns in the vault state. "proposals": turns that '
            'proposed an action or made a recommendation. "pushback": turns that disagreed with '
            "the user or named a contradiction in what the user asked. A turn may be in both. "
            "Use only listed indexes and [] when none."
        ]
    return [
        '- "turns": an object {"proposals": n, "pushback": n} of integers, counted over the '
        'assistant turns in the digest. "proposals": turns that proposed an action or made a '
        'recommendation. "pushback": turns that disagreed with the user or named a '
        "contradiction in what the user asked. Always output it; use 0 when none."
    ]


def state(ctx: Ctx) -> str:
    if ctx.turns is None:
        return ""
    shown = f"{len(ctx.turns)} of {ctx.turns_total}" if ctx.turns else "0"
    lines = [f"{i}. {text}" for i, text in ctx.turns]
    return f"Assistant turns ({shown}, numbered, oldest first):\n" + "\n".join(lines)


def valid(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_COUNT


def counted(ctx: Ctx, turns: dict[str, Any]) -> dict[str, int] | None:
    if ctx.turns is None:
        counts = {k: turns.get(k) for k in NAMES}
        ok = all(valid(v) for v in counts.values())
        return {k: int(v) for k, v in counts.items() if isinstance(v, int)} if ok else None
    shown = {i for i, _ in ctx.turns}
    found: dict[str, int] = {}
    for name in NAMES:
        raw = turns.get(name)
        if not (isinstance(raw, list) and all(isinstance(i, int) and i in shown for i in raw)):
            return None
        found[name] = len(set(raw))
    return found


def apply(ctx: Ctx, data: dict[str, Any], sections: dict[str, str]) -> None:
    turns = data.get(KEY)
    if not isinstance(turns, dict):
        return
    counts = counted(ctx, turns)
    if counts is None or counts["pushback"] > counts["proposals"]:
        sections[KEY] = "dropped:invalid"
        return
    ctx.result[KEY] = counts
    if ctx.turns is not None:
        ctx.result.update(turns_seen=len(ctx.turns), turns_total=ctx.turns_total)
    sections[KEY] = "ok"
