import os
from typing import Any

from juno import config
from juno.adapters import ledgerio, status
from juno.core import routing
from juno.core.ledger import Segment
from juno.core.redact import chunks, redact
from juno.handlers.common import main_session, output


def should_remind(cfg: config.Config, seg: Segment, payload: dict[str, Any]) -> bool:
    if (
        not seg.dirty
        or seg.nosave
        or seg.private
        or payload.get("stop_hook_active")
        or payload.get("background_tasks")
    ):
        return False
    spacing = cfg.get_int("thresholds", "reminder_spacing_turns")
    return seg.last_remind_turn is None or seg.turns - seg.last_remind_turn >= spacing


def reminder_text(cfg: config.Config, seg: Segment) -> str:
    id8 = seg.sid[:8]
    lines = [
        f"Juno: the handoff card for segment {id8} is due (repos: {', '.join(seg.repos) or '-'}; "
        f"tickets: {', '.join(seg.tickets) or '-'}; {seg.edit_total} edits, "
        f"{seg.tools} tool calls, {seg.turns} user turns)."
    ]
    for target in routing.card_targets(seg):
        path = os.path.join(cfg.vault, *target["path"].split("/"))
        how = "exists" if os.path.exists(path) else "does not exist yet"
        lines.append(f"Card path: {path} ({how}; heading key {target['key']}).")
    lines.append(
        'Write it with `juno card --label L --learned "<one sentence or none>" '
        '"<what was done>" "<where it stopped and what is next>"` (2-5 lines; '
        "`--decision` and `--links` optional); it adds or replaces this segment's card. "
        "Do not use Edit or Write on vault files; they are refused."
    )
    return "\n".join(lines)


def handle(payload: dict[str, Any], cfg: config.Config) -> dict[str, Any] | None:
    if not main_session(payload):
        return None
    seg = ledgerio.fold(cfg, payload.get("session_id"))
    message = payload.get("last_assistant_message")
    if isinstance(message, str) and not seg.private:
        limit = cfg.get_int("caps", "last_message_chars")
        excerpt = chunks(redact(message[:limit]))[0]
        ledgerio.record(cfg, seg, {"ev": "stop", "msg": excerpt})
    if cfg.get_str("handoff", "mode") != "reminder" or not should_remind(cfg, seg, payload):
        status.update(cfg, seg)
        return None
    ledgerio.record(cfg, seg, {"ev": "remind"})
    status.update(cfg, seg, reminded=True)
    return output(cfg, "Stop", reminder_text(cfg, seg))
