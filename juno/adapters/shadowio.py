import contextlib
import os
import time
from typing import Any

from juno import config
from juno.adapters import armsio, fs, keysio, repos, store
from juno.core import packs, prompts, shadow
from juno.core.ledger import Segment
from juno.core.view import VaultView

SHADOW_FILE = "shadow.jsonl"
DAY = 86400
OVERFETCH = 15


def shadow_path(state: str) -> str:
    return os.path.join(state, SHADOW_FILE)


def append(state: str, entry: shadow.Entry) -> None:
    fs.ensure_private_dir(state)
    data = (shadow.dump(entry) + "\n").encode("utf-8")
    fd = os.open(shadow_path(state), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if os.write(fd, data) != len(data):
            raise OSError("short write to shadow log")
    finally:
        os.close(fd)


def read(state: str) -> tuple[list[shadow.Entry], int]:
    try:
        with open(shadow_path(state), encoding="utf-8", errors="replace") as handle:
            return shadow.parse_lines(handle)
    except FileNotFoundError:
        return [], 0


def rewrite(state: str, entries: list[shadow.Entry]) -> None:
    text = "".join(shadow.dump(e) + "\n" for e in entries)
    fs.atomic_write(shadow_path(state), text.encode("utf-8"), 0o600)


def prune(state: str, days: int, now: float | None = None) -> tuple[int, int]:
    if not os.path.exists(shadow_path(state)):
        return 0, 0
    cutoff = (time.time() if now is None else now) - days * DAY
    entries, bad = read(state)
    keep = [e for e in entries if e["ts"] >= cutoff]
    if len(keep) != len(entries) or bad:
        rewrite(state, keep)
    return len(keep), len(entries) - len(keep) + bad


def merge_log(state: str, source: str) -> dict[str, int]:
    try:
        with open(source, encoding="utf-8", errors="replace") as handle:
            incoming, bad = shadow.parse_lines(handle)
    except FileNotFoundError:
        return {"added": 0, "skipped": 0, "invalid": 0, "total": len(read(state)[0])}
    existing, _ = read(state)
    merged, added, skipped = shadow.merge(existing, incoming)
    if added:
        rewrite(state, merged)
    return {"added": added, "skipped": skipped, "invalid": bad, "total": len(merged)}


def search_hits(
    db: str,
    query: str,
    preset: str,
    seconds: float,
    ticket_files: bool,
    keys_weight: float = 0.0,
    with_alt: bool = False,
) -> tuple[list[dict[str, Any]], list[str]] | None:
    import threading

    result: dict[str, tuple[list[dict[str, Any]], list[str]]] = {}

    def ranked(weight: float) -> list[dict[str, Any]]:
        from juno.adapters import indexquery

        hits = indexquery.search(
            db, query, limit=OVERFETCH, preset=preset, ticket_files=ticket_files, keys_weight=weight
        )
        return [h for h in hits if shadow.suggestable(h["relpath"])][: shadow.SHOWN_SUGGESTIONS]

    def work() -> None:
        import sqlite3

        with contextlib.suppress(FileNotFoundError, RuntimeError, sqlite3.Error):
            hits = ranked(keys_weight)
            alt = [h["relpath"] for h in ranked(0.0)] if with_alt and keys_weight > 0 else []
            result["hits"] = (hits, alt)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(seconds)
    return None if worker.is_alive() else result.get("hits")


def pack_extras(
    cfg: config.Config,
    seg: Segment,
    view: VaultView,
    tickets: list[str],
    before: set[str],
    pack_text: str,
    own: str,
) -> dict[str, Any]:
    sources: dict[str, list[str]] = {}
    for key in tickets:
        src = packs.pack_sources("ticket", key)
        sources[key] = [p for p in (src["doc"], src["rules"]) if view.exists(p)]
    delivered, carried = shadow.delivered_paths(tickets, before, seg.packs, sources, pack_text)
    finder = repos.tickets_from(cfg).find
    outside = set(finder(prompts.strip_pastes(prompts.PASTED_CONTENT_RE.sub("", own))))
    pasted = [k for k in finder(own) if k not in outside]
    arms = armsio.arms_for(cfg, seg.sid)
    return {"delivered": delivered, "carried": carried, "pasted": pasted, "arms": arms}


def observe(
    cfg: config.Config,
    seg: Segment,
    view: VaultView,
    typed: str,
    own: str,
    nosave: bool,
    tickets: list[str],
    before: set[str],
    pack_text: str,
) -> None:
    if not cfg.get_bool("shadow", "enabled") or nosave or not typed.strip():
        return
    try:
        db = os.path.join(cfg.state, "index.sqlite3")
        if not os.path.exists(db):
            return
        budget = cfg.get_int("shadow", "budget_ms") / 1000
        found = search_hits(
            db,
            typed,
            cfg.get_str("language", "preset"),
            budget,
            bool(cfg.get_list("jira", "prefixes")),
            cfg.get_float("keys", "weight"),
            os.path.exists(keysio.keys_path(cfg.state)),
        )
        if found is None:
            return
        hits, alt = found
        extras = pack_extras(cfg, seg, view, tickets, before, pack_text, own)
        entry = shadow.build_entry(int(time.time()), seg.sid, typed, hits, alt=alt, **extras)
        append(cfg.state, entry)
    except Exception as error:
        store.log_error(cfg, "UserPromptSubmit.shadow", error)
