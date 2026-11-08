from typing import Any

from juno.adapters import cardwrite, store
from juno.adapters.learnkinds.base import Ctx, status_of, today, vault_text
from juno.core import extract_place
from juno.core.digest import clip
from juno.core.textchecks import as_list

KEY = "pitfalls"
SHAPE = list
LOCKED = True
STATE_RANK = 2
STATE_CHARS = 1500


def instructions(ctx: Ctx) -> list[str]:
    return [
        '- "pitfalls": at most 2 {"text", "evidence", "triggers"}: Claude\'s own mistakes '
        "visible in the digest (mostly Learning moments) with the verified correct behaviour, "
        'such as a wrong profile, branch or directory and the right one. "text": one neutral '
        "sentence, 20-300 chars, no `·`, no second person; name the check to reuse. "
        '"evidence": the command, result or file:line copied from the digest, 4-200 chars. '
        '"triggers": 0-5 whole words, commands or path terms that mark when the lesson applies; '
        "use [] only for a lesson that applies to every session. Not repo facts (those are "
        'gotchas in "knowledge"), not user preferences (rules), not duplicates of the existing '
        "pitfall lines in the vault state."
    ]


def state(ctx: Ctx) -> str:
    rows = "\n".join(
        row for row in vault_text(ctx.cfg, "pitfalls.md").splitlines() if row.startswith("- ")
    )
    return "Existing pitfall lines:\n" + (clip(rows, STATE_CHARS) or "-")


def apply(ctx: Ctx, data: dict[str, Any], sections: dict[str, str]) -> None:
    items = as_list(data.get(KEY))
    if not items:
        return
    path = cardwrite.safe_path(ctx.cfg, "pitfalls.md")
    if path is None:
        sections[KEY] = "dropped:path"
        return
    text, written, dropped = extract_place.apply_pitfalls(
        store.read_text(path), items, ctx.digest or "", ctx.seg.sid[:8], today()
    )
    if text is not None:
        store.backup(ctx.cfg, path)
        store.write_text(path, text)
    ctx.result[KEY] = {"written": written, "dropped": dropped}
    sections[KEY] = status_of(len(items), len(written), dropped)
