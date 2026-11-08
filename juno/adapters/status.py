import json
import os
import time
from collections.abc import Iterator, Mapping
from typing import Any

from juno import config
from juno.adapters import fs, store
from juno.core import routing
from juno.core import status as core_status
from juno.core.ledger import Segment

DAY = 86400


def error_rows(cfg: config.Config) -> Iterator[tuple[float, list[str]]]:
    for line in store.read_text(os.path.join(cfg.state, "hook-errors.log")).splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            stamp = time.mktime(time.strptime(parts[0][:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            continue
        yield stamp, parts


def error_summary(
    cfg: config.Config, now: float | None = None
) -> tuple[int, dict[str, str] | None]:
    now = now or time.time()
    count = 0
    last: dict[str, str] | None = None
    for stamp, parts in error_rows(cfg):
        if now - stamp <= DAY:
            count += 1
        last = {"ts": parts[0], "kind": parts[2]}
    return count, last


def repeated_errors(
    cfg: config.Config, now: float | None = None
) -> list[tuple[tuple[str, str, str], int]]:
    now = now or time.time()
    window = cfg.get_int("thresholds", "hook_error_window_hours") * 3600
    counts: dict[tuple[str, str, str], int] = {}
    for stamp, parts in error_rows(cfg):
        if now - stamp <= window:
            key = (parts[1], parts[2], parts[3] if len(parts) > 3 else "-")
            counts[key] = counts.get(key, 0) + 1
    threshold = cfg.get_int("thresholds", "hook_error_repeat")
    repeated = sorted(((n, key) for key, n in counts.items() if n >= threshold), reverse=True)
    return [(key, n) for n, key in repeated]


def read_status(state_dir: str, sid: str | None = None) -> dict[str, Any]:
    shared = os.path.join(state_dir, "status.json")
    own = core_status.session_status_path(state_dir, sid)
    if own is None:
        return dict(store.read_json_dict(shared))
    data = store.read_json_dict(own)
    if data:
        return dict(data)
    data = store.read_json_dict(shared)
    return dict(data) if data.get("segment_id") == sid else {}


def write_status(state_dir: str, status: dict[str, Any]) -> None:
    text = (json.dumps(status, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    own = core_status.session_status_path(state_dir, status.get("segment_id"))
    if own is not None:
        fs.ensure_private_dir(os.path.dirname(own))
        fs.atomic_write(own, text, 0o600)
    fs.ensure_private_dir(state_dir)
    fs.atomic_write(os.path.join(state_dir, "status.json"), text, 0o600)


def prune_session_files(state_dir: str, now: float) -> None:
    directory = os.path.join(state_dir, "status")
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        return
    for name in names:
        path = os.path.join(directory, name)
        try:
            if now - os.stat(path).st_mtime > core_status.SESSION_FILE_SECONDS:
                os.unlink(path)
        except OSError:
            continue


def update(
    cfg: config.Config,
    seg: Segment,
    rule: Mapping[str, Any] | None = None,
    reminded: bool = False,
) -> dict[str, Any]:
    errors, last_error = error_summary(cfg)
    targets = routing.card_targets(seg) if seg.sid else []
    status = core_status.build_status(
        sid=seg.sid,
        now=time.time(),
        previous=read_status(cfg.state, seg.sid),
        repo=seg.last_repo,
        ticket=seg.current_ticket(),
        card_dirty=bool(seg.dirty),
        card_auto=cfg.get_str("handoff", "mode") == "end",
        card_path=os.path.join(cfg.vault, *targets[0]["path"].split("/")) if targets else None,
        packs_injected=seg.full_packs,
        packs_cap=cfg.get_int("caps", "max_full_packs"),
        rules_added_segment=seg.rules_added,
        errors_24h=errors,
        last_error=last_error,
        rule=rule,
        reminded=reminded,
    )
    write_status(cfg.state, status)
    return status
