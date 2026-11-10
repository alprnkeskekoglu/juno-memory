import time
from typing import Any

from juno.adapters import cardwrite, queues, store
from juno.adapters.learnkinds.base import Ctx, status_of, today, vault_text
from juno.core import candidates, extract, extract_place
from juno.core.textchecks import as_list
from juno.core.vaultfmt import THREAD_RE

KEY = "threads"
SHAPE = dict
LOCKED = False
STATE_RANK = 3


def instructions(ctx: Ctx) -> list[str]:
    return [
        '- "threads": {"open": [{"tag": "<a ticket or repo of this segment>", "text": "<an open '
        'question that outlives this segment>"}], "close": [{"line": "<an open thread line from '
        'the vault state, copied exactly>", "reason": "<one line, 8-160 chars>", "evidence": '
        '"<4-200 chars copied from the digest that shows it>"}]}; at most one open. "close" '
        "only proposes: the user decides at the next session start. Propose a thread only when "
        "the digest clearly shows it fixed, answered or dropped by what the user did or chose, "
        "superseded by a later decision or state (for example a version it says is old is now "
        "current), or a near-duplicate of another open thread (propose the older or weaker "
        "one). At most 2; when unsure, propose none and the thread stays open."
    ]


def state(ctx: Ctx) -> str:
    rows = [
        row
        for row in vault_text(ctx.cfg, "threads.md").splitlines()
        if THREAD_RE.match(row.rstrip())
    ]
    return "Open thread lines:\n" + ("\n".join(rows) or "-")


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
    try:
        with cardwrite.vault_lock(ctx.cfg):
            text, out = extract_place.apply_threads(
                store.read_text(path),
                threads,
                extract.segment_tags(list(ctx.seg.repos), list(ctx.seg.tickets)),
                today(),
                ctx.digest or "",
            )
            if text is not None:
                store.backup(ctx.cfg, path)
                store.write_text(path, text)
    except TimeoutError:
        sections[KEY] = "dropped:vault lock busy" if proposed else "none"
        return
    out["proposed"] = len(out["proposals"])
    if out["proposals"]:
        out["queued"] = queues.update(
            ctx.cfg,
            queues.THREADS,
            lambda queue: candidates.add_proposals(
                queue, out["proposals"], ctx.seg.sid, ctx.now or time.time()
            ),
        )
    ctx.result[KEY] = {k: v for k, v in out.items() if k != "proposals"}
    done = len(out["opened"]) + len(out["updated"]) + out["proposed"]
    sections[KEY] = status_of(proposed, done, {"invalid": out["dropped"]})
