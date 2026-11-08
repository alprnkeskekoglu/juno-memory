import os
from typing import Any

from juno.adapters import cardwrite, queues, store
from juno.adapters.learnkinds.base import STATE_TAIL, Ctx, list_md, status_of, vault_text
from juno.core import backlog, candidates, suggest, vaultfmt
from juno.core.textchecks import as_list

KEY = "backlog"
SHAPE = list
LOCKED = False
STATE_RANK = STATE_TAIL


def rows(ctx: Ctx) -> list[suggest.Row]:
    found: list[suggest.Row] = []
    for name in list_md(ctx.cfg, "backlog"):
        path = cardwrite.safe_path(ctx.cfg, "backlog", name)
        if name.startswith(".") or not path:
            continue
        row = backlog.item_row(name[:-3], store.read_text(path))
        if row["problem"] or row["status"] in backlog.CLOSED:
            continue
        found.append(row)
    return sorted(found, key=backlog.order_key)


def items(ctx: Ctx) -> list[suggest.Row]:
    if ctx.digest is None or not os.path.isdir(os.path.join(ctx.cfg.vault, "backlog")):
        return []
    seg = ctx.seg
    return suggest.relevant_items(rows(ctx), list(seg.repos), list(seg.tickets), ctx.digest)


def instructions(ctx: Ctx) -> list[str]:
    return suggest.instruction(items(ctx))


def state(ctx: Ctx) -> str:
    return suggest.state_text(items(ctx))


def done_tickets(ctx: Ctx, listed: list[suggest.Row]) -> set[str]:
    keys = {k for r in listed for k in r["tickets"]}
    done = set()
    for key in keys:
        fields = vaultfmt.parse_frontmatter(vault_text(ctx.cfg, f"tasks/{key.upper()}.md"))[0]
        if vaultfmt.field_str(fields, "status") == "done":
            done.add(key)
    return done


def queue_entries(ctx: Ctx, entries: list[object]) -> dict[str, int]:
    digest = ctx.digest or ""
    listed = {r["slug"]: r for r in items(ctx)}
    picked, dropped = suggest.pick(
        entries, listed, digest, done_tickets(ctx, list(listed.values()))
    )
    out = {"proposed": len(entries), "queued": 0, "dropped": dropped}
    if picked:

        def change(queue: list[candidates.Item]) -> tuple[int, int]:
            queue[:] = candidates.prune(queue, ctx.now)
            return candidates.add_backlog(queue, picked, ctx.seg.sid, ctx.now, ctx.seg.rules.preset)

        out["queued"], more = queues.update(ctx.cfg, queues.BACKLOG, change)
        out["dropped"] += more
    return out


def apply(ctx: Ctx, data: dict[str, Any], sections: dict[str, str]) -> None:
    entries = as_list(data.get(KEY))
    if not entries:
        return
    out = queue_entries(ctx, entries)
    ctx.result[KEY] = out
    sections[KEY] = status_of(len(entries), out["queued"], {"invalid": out["dropped"]})
