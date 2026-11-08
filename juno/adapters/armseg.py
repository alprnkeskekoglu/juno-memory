import json
import os
import time
from collections.abc import Callable
from typing import Any

from juno import config
from juno.adapters import armsio, evalio, fs, ledgerio, nextio, selfio, usageio
from juno.core import armmetrics, fold

Counter = Callable[[config.Config, str, str], dict[str, Any]]


def reads_of(cfg: config.Config, sid: str) -> list[tuple[float, str]] | None:
    return usageio.read_touches(cfg.state, sid) or evalio.read_touches(cfg, sid)


def linked_counters(cfg: config.Config, sid: str, name: str) -> dict[str, Any]:
    packs, _ = evalio.ledger_sid_data(cfg, sid)
    filled = [(stamp, set(nb)) for _, stamp, nb in packs if nb]
    if not filled:
        return {"nb_units": 0}
    touches = reads_of(cfg, sid)
    counts: dict[str, Any] = {"nb_units": len(filled)}
    if touches is not None:
        counts["nb_read"] = int(armmetrics.read_after(filled, touches))
    return counts


def next_counters(cfg: config.Config, sid: str, name: str) -> dict[str, Any]:
    events = evalio.ledger_next_events(cfg, sid)
    if not events:
        return {"units": 0}
    since, seg8 = min(events)
    counts: dict[str, Any] = {"units": 1}
    path = evalio.find_transcript(evalio.transcripts_dir(cfg), sid)
    if path:
        vault = os.path.realpath(cfg.vault)
        counts["lookups"] = evalio.early_lookups(path, vault, since)
    asked = [p["q"] for p in nextio.load(cfg).get(seg8, [])]
    typed = list(ledgerio.fold(cfg, sid).prompts.values())[: armmetrics.HIT_PROMPTS]
    preset = cfg.get_str("language", "preset")
    counts["hit"] = int(armmetrics.predicted(asked, typed, preset))
    return counts


COUNTERS: dict[str, Counter] = {
    "linked": linked_counters,
    "next": next_counters,
    "self": selfio.counters,
}


def build(cfg: config.Config, sid: str, stamp: float) -> dict[str, Any] | None:
    _, sides = evalio.ledger_sid_data(cfg, sid)
    if not sides:
        return None
    cues = cfg.language_list("correction_cues")
    preset = cfg.get_str("language", "preset")
    typed = list(ledgerio.fold(cfg, sid).prompts.values())
    counters = {n: COUNTERS[n](cfg, sid, n) for n in sides if n in COUNTERS}
    return {
        "t": stamp,
        "sid": sid,
        "arms": sides,
        "prompts": len(typed),
        "cues": sum(fold.has_cue(text, cues, preset) for text in typed),
        "counters": counters,
    }


def sweep(cfg: config.Config, now: float | None = None, force: bool = False) -> int:
    if not armsio.running(cfg):
        return 0
    now = time.time() if now is None else now
    quiet = now - cfg.get_int("thresholds", "stub_quiet_seconds")
    rows = armsio.read_segments(cfg)
    written = 0
    for sid in ledgerio.ledger_ids(cfg):
        stamp = ledgerio.mtime(cfg, sid)
        if stamp <= float(rows.get(sid, {}).get("t") or 0) or (not force and stamp > quiet):
            continue
        row = build(cfg, sid, stamp)
        if row is None:
            continue
        fs.append_log(
            armsio.segments_path(cfg), json.dumps(row, ensure_ascii=False, sort_keys=True), 1 << 26
        )
        written += 1
    return written
