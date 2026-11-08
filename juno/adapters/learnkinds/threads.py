from typing import Any

from juno.adapters import cardwrite, store
from juno.adapters.learnkinds.base import Ctx, status_of, today, vault_text
from juno.core import extract, extract_place
from juno.core.textchecks import as_list

KEY = "threads"
SHAPE = dict
LOCKED = True
STATE_RANK = 3


def instructions(ctx: Ctx) -> list[str]:
    return [
        '- "threads": {"open": [{"tag": "<a ticket or repo of this segment>", "text": "<an open '
        'question that outlives this segment>"}], "close": ["<a thread line from the vault '
        'state, copied exactly, that this segment resolved>"]}; at most one open.'
    ]


def state(ctx: Ctx) -> str:
    tags = extract.segment_tags(list(ctx.seg.repos), list(ctx.seg.tickets))
    rows = [
        row for row in vault_text(ctx.cfg, "threads.md").splitlines() if extract.related(row, tags)
    ]
    return "Thread lines tagged with or naming this segment's repos and tickets:\n" + (
        "\n".join(rows) or "-"
    )


def apply(ctx: Ctx, data: dict[str, Any], sections: dict[str, str]) -> None:
    raw = data.get(KEY)
    if raw is None:
        return
    threads = raw if isinstance(raw, dict) else None
    if threads is None:
        sections[KEY] = "dropped:invalid"
        return
    proposed = len(as_list(threads.get("open"))) + len(as_list(threads.get("close")))
    path = cardwrite.safe_path(ctx.cfg, "threads.md")
    if path is None:
        sections[KEY] = "dropped:path" if proposed else "none"
        return
    text, out = extract_place.apply_threads(
        store.read_text(path),
        threads,
        extract.segment_tags(list(ctx.seg.repos), list(ctx.seg.tickets)),
        today(),
    )
    if text is not None:
        store.backup(ctx.cfg, path)
        store.write_text(path, text)
    ctx.result[KEY] = out
    done = len(out["opened"]) + len(out["closed"])
    sections[KEY] = status_of(proposed, done, {"invalid": out["dropped"]})
