from typing import Any

from juno.adapters import gate, taskio
from juno.adapters.learnkinds.base import Ctx, status_of, today
from juno.core import taskdigest, ticketdigest, ticketwork, vaultfmt, writegate
from juno.core.digest import clip
from juno.core.errors import Refused
from juno.core.taskrows import Row
from juno.core.textchecks import anchored, as_list, text_field

KEY = "ticket"
SHAPE = list
LOCKED = True
STATE_RANK = 6
STATE_CHARS = 1200
EVIDENCE = "engine: ticket run"


def keys_of(ctx: Ctx) -> list[str]:
    if ctx.cfg.get_int("handoff", "ticket_chars") <= 0:
        return []
    return ticketwork.worked(
        ctx.seg, ticketwork.has_pr(ctx.digest or ""), ticketdigest.blocks_of(ctx.digest or "")
    )


def instructions(ctx: Ctx) -> list[str]:
    keys = keys_of(ctx)
    if not keys:
        return []
    return [
        f'- "ticket": at most {ticketwork.MAX_TICKETS} {{"key", "lines"}} for the tickets this '
        f'segment worked on ({", ".join(keys)}); "lines" holds at most {ticketwork.MAX_LINES} '
        '{"section", "key", "text", "evidence"} that fill the ticket file with the facts this '
        "segment showed. Sections "
        'and keys: "Summary"/"summary" (what the ticket is, the cause in one sentence and the '
        'chosen approach, in full); "Criteria"/"ac1", "ac2", ... (the full text of each '
        'acceptance criterion); "Work"/"<repo name>" (branch, PR URL, review result); '
        '"Tests"/"outcome" (round, counts, the reason for every failed or blocked case) and '
        '"Tests"/"case-<id>" (what the case checks and its result); "Cause"/"cause" (bugs only: '
        'the root cause and where it was introduced). "text": 8-1000 chars on one line, the '
        "facts themselves in full, no `·`. Never a path or pointer to a file that is not in "
        "the vault, "
        'never a bare count that stands for content (write what the criteria are, not "5 '
        'ACs"), never a guess. "evidence": 4-200 chars copied from the digest (the Ticket '
        "block, a prompt or a command) that shows it. Reuse a key listed in the vault state to "
        "replace that line when its content changed; skip lines that did not change. No user "
        "decisions and no status. Use [] when the segment only mentioned a ticket."
    ]


def state(ctx: Ctx) -> str:
    parts: list[str] = []
    for key in keys_of(ctx):
        text = taskio.read(ctx.cfg, key) or ""
        body = "\n".join(
            f"## {name}\n{vaultfmt.section(text, name)}"
            for name in dict.fromkeys(taskdigest.listed(text))
            if taskdigest.valid_section(name) and vaultfmt.section(text, name)
        )
        parts.append(f"{taskio.rel(key)} digest lines:\n" + (clip(body, STATE_CHARS) or "-"))
    return "\n\n".join(parts)


def accept(ctx: Ctx, text: str, evidence: str) -> str | None:
    if not anchored(evidence, ctx.digest or ""):
        return "unanchored"
    try:
        gate.scan_line(ctx.cfg, text, "line", writegate.TASK_LINE_MAX)
    except Refused:
        return "private"
    return None


def tally(into: dict[str, int], reasons: dict[str, int]) -> None:
    for reason, count in reasons.items():
        into[reason] = into.get(reason, 0) + count


def one(ctx: Ctx, item: object, keys: list[str]) -> tuple[int, list[Row], dict[str, int], str]:
    """(lines proposed, rows to write, dropped reasons, key) of one ticket item."""
    if not isinstance(item, dict):
        return 1, [], {"invalid": 1}, ""
    key = text_field(item.get("key")).upper()
    lines = as_list(item.get("lines"))
    if key not in keys:
        return max(len(lines), 1), [], {"unworked": max(len(lines), 1)}, key
    rows, dropped = ticketwork.check_all(
        lines, ctx.cfg.vault, lambda text, evidence: accept(ctx, text, evidence)
    )
    return len(lines[: ticketwork.MAX_LINES]), rows, dropped, key


def apply(ctx: Ctx, data: dict[str, Any], sections: dict[str, str]) -> None:
    items = as_list(data.get(KEY))[: ticketwork.MAX_TICKETS]
    if not items:
        return
    keys = keys_of(ctx)
    proposed = done = 0
    dropped: dict[str, int] = {}
    written: dict[str, int] = {}
    for item in items:
        count, rows, reasons, key = one(ctx, item, keys)
        proposed += count
        tally(dropped, reasons)
        if not rows or key in written:
            continue
        try:
            taskio.put_rows(ctx.cfg, ctx.seg, key, rows, EVIDENCE, today(), True)
        except Refused:
            tally(dropped, {"path": len(rows)})
            continue
        written[key] = len(rows)
        done += len(rows)
    ctx.result[KEY] = {"written": written, "dropped": dropped}
    sections[KEY] = status_of(proposed, done, dropped)
