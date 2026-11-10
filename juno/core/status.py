import os
import re
import time
from collections.abc import Mapping
from typing import Any

STATUS_VERSION = 1
SESSION_FILE_SECONDS = 86400
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def session_status_path(state_dir: str, sid: object) -> str | None:
    if not isinstance(sid, str) or not SAFE_ID.match(sid):
        return None
    return os.path.join(state_dir, "status", sid + ".json")


def build_status(
    *,
    sid: str | None,
    now: float,
    previous: Mapping[str, Any],
    repo: str | None,
    ticket: str | None,
    card_dirty: bool,
    card_auto: bool,
    card_path: str | None,
    packs_injected: int,
    packs_cap: int,
    rules_added_segment: int,
    writes: int,
    write_cap: int,
    errors_24h: int,
    last_error: Mapping[str, Any] | None,
    rule: Mapping[str, Any] | None = None,
    reminded: bool = False,
) -> dict[str, Any]:
    return {
        "v": STATUS_VERSION,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
        "segment_id": sid,
        "interactive": True,
        "repo": repo,
        "ticket": ticket,
        "card_dirty": bool(card_dirty),
        "card_auto": bool(card_auto),
        "card_path": card_path,
        "packs_injected": packs_injected,
        "packs_cap": packs_cap,
        "rules_added_segment": rules_added_segment,
        "writes": writes,
        "write_cap": write_cap,
        "rules_added_seq": int(previous.get("rules_added_seq") or 0) + (1 if rule else 0),
        "last_rule": dict(rule) if rule else previous.get("last_rule"),
        "last_reminder_seq": int(previous.get("last_reminder_seq") or 0) + (1 if reminded else 0),
        "errors_24h": errors_24h,
        "last_error": dict(last_error) if last_error else None,
    }
