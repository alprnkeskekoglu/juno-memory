import json
import os
import time

from juno import config
from juno.adapters import cardjob, fs, gate, ledgerio, store
from juno.core import routing, vaultfmt
from juno.core.errors import Refused
from juno.core.ledger import Segment

LATE_RUNS_PER_PASS = 2
STUB_MARKER = "stub-pass.json"


def has_card_for(path: str, seg_id8: str) -> bool:
    return any(c.get("seg") == seg_id8 for c in vaultfmt.parse_cards(store.read_text(path), ""))


def append_card(existing: str, card: str, name: str) -> str:
    if not existing:
        existing = f"# Handoffs · {name}\n"
    if not existing.endswith("\n"):
        existing += "\n"
    return existing + "\n" + card


def write_stub(cfg: config.Config, seg: Segment, now: float) -> list[str]:
    written: list[str] = []
    for target in routing.card_targets(seg):
        rel = str(target["path"])
        try:
            with fs.locked(os.path.join(cfg.state, "vault.lock"), 2.0):
                path = gate.vault_file(cfg, rel)
                if not has_card_for(path, seg.sid[:8]):
                    card = routing.stub_card(
                        seg,
                        target,
                        now,
                        cfg.get_int("caps", "stub_excerpt_chars"),
                        cfg.get_list("jira", "prefixes"),
                        cardjob.card_failure(cfg, seg.sid),
                    )
                    new = append_card(store.read_text(path), card, os.path.basename(path)[:-3])
                    gate.commit(
                        cfg, seg, "card", rel, new, f"segment {seg.sid[:8]} stub", run_cap=False
                    )
        except (TimeoutError, Refused) as exc:
            store.log_error(cfg, "stub-card", exc)
            continue
        written.append(path)
    return written


def read_marker(cfg: config.Config) -> tuple[float, set[str]]:
    data = store.read_json_dict(os.path.join(cfg.state, STUB_MARKER))
    try:
        pending = data.get("pending") or []
        stamp = data.get("t") or 0
        assert isinstance(pending, list)
        return float(str(stamp)), {str(p) for p in pending}
    except ValueError, AssertionError:
        return 0.0, set()


def stub_ended_segments(
    cfg: config.Config, current_sid: str, now: float | None = None
) -> tuple[Segment, list[str]] | None:
    now = now or time.time()
    if not os.path.isdir(cfg.vault):
        return None
    since, pending = read_marker(cfg)
    pass_started = time.time()
    newest: tuple[Segment, list[str]] | None = None
    late = 0
    quiet = cfg.get_int("thresholds", "stub_quiet_seconds")
    for sid in ledgerio.ledger_ids(cfg):
        if sid == current_sid:
            continue
        stamp = ledgerio.mtime(cfg, sid)
        if sid not in pending and stamp < since:
            continue
        if now - stamp < quiet or cardjob.job_active(cfg, sid):
            pending.add(sid)
            continue
        seg = ledgerio.fold(cfg, sid)
        if not cardjob.needs_stub(seg):
            continue
        if late < LATE_RUNS_PER_PASS and cardjob.late_run(cfg, seg):
            late += 1
            pending.add(sid)
            continue
        paths = write_stub(cfg, seg, now)
        if not paths:
            pending.add(sid)
            continue
        ledgerio.append(
            cfg,
            sid,
            {"ev": "card", "path": ",".join(store.rel(cfg, p) for p in paths), "auto": True},
        )
        if newest is None or (seg.last_t or 0) > (newest[0].last_t or 0):
            newest = (seg, paths)
    retry = sorted(
        p for p in pending if p != current_sid and cardjob.needs_stub(ledgerio.fold(cfg, p))
    )
    marker = {"t": pass_started, "pending": [*retry, current_sid]}
    store.state_dir(cfg)
    fs.atomic_write(
        os.path.join(cfg.state, STUB_MARKER), (json.dumps(marker) + "\n").encode(), 0o600
    )
    return newest
