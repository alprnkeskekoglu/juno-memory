import os
import time

from juno import config
from juno.adapters import fs, repos, store
from juno.core import ledger
from juno.core.ledger import Record, Segment


def stamp() -> float:
    return round(time.time(), 3)


def rules_from(cfg: config.Config) -> ledger.Rules:
    return ledger.Rules(
        meaningful_tool_calls=cfg.get_int("thresholds", "meaningful_tool_calls"),
        meaningful_user_turns=cfg.get_int("thresholds", "meaningful_user_turns"),
        dirty_user_turns=cfg.get_int("thresholds", "dirty_user_turns"),
        dirty_tool_calls=cfg.get_int("thresholds", "dirty_tool_calls"),
        preset=cfg.get_str("language", "preset"),
        tickets=repos.tickets_from(cfg),
    )


def ledger_dir(cfg: config.Config) -> str:
    return os.path.join(cfg.state, "ledgers")


def ledger_path(cfg: config.Config, sid: object) -> str | None:
    if not ledger.safe_id(sid):
        return None
    return os.path.join(ledger_dir(cfg), f"{sid}.jsonl")


def append(cfg: config.Config, sid: object, record: Record) -> Record | None:
    path = ledger_path(cfg, sid)
    if path is None:
        return None
    fs.ensure_private_dir(os.path.dirname(path))
    record = dict(record)
    record.setdefault("t", stamp())
    record, data, shrunk = ledger.encode_capped(record)
    if shrunk:
        store.bump_counter(cfg, "ledger_truncated")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return record


def fold(cfg: config.Config, sid: object) -> Segment:
    path = ledger_path(cfg, sid)
    if path is None:
        return Segment(str(sid), rules_from(cfg))
    data = fs.read_bytes(path)
    return ledger.fold_lines(str(sid), (data or b"").splitlines(), rules_from(cfg))


def record(cfg: config.Config, seg: Segment, rec: Record) -> Record | None:
    written = append(cfg, seg.sid, rec)
    if written:
        seg.apply(written)
    return written


def exists(cfg: config.Config, sid: object) -> bool:
    path = ledger_path(cfg, sid)
    return path is not None and os.path.isfile(path)


def ledger_ids(cfg: config.Config) -> list[str]:
    try:
        names = os.listdir(ledger_dir(cfg))
    except OSError:
        return []
    return [n[:-6] for n in names if n.endswith(".jsonl")]


def mtime(cfg: config.Config, sid: str) -> float:
    path = ledger_path(cfg, sid)
    try:
        return os.stat(path).st_mtime if path else 0.0
    except OSError:
        return 0.0


def synthetic_report(cfg: config.Config) -> dict[str, int]:
    found = {"ledgers": 0, "prompts": 0, "records": 0}
    for sid in ledger_ids(cfg):
        path = ledger_path(cfg, sid)
        raw = fs.read_bytes(path) if path else None
        if raw is None:
            continue
        _, dropped, pids = ledger.scan_synthetic(raw.decode("utf-8", "replace"))
        if dropped:
            found["ledgers"] += 1
            found["prompts"] += len(pids)
            found["records"] += dropped
    return found


def purge_synthetic(cfg: config.Config) -> dict[str, object]:
    done: dict[str, object] = {"ledgers": 0, "prompts": 0, "records": 0, "busy": []}
    busy: list[str] = []
    for sid in ledger_ids(cfg):
        path = ledger_path(cfg, sid)
        if path is None:
            continue
        for _ in range(3):
            raw = fs.read_bytes(path)
            if raw is None:
                break
            kept, dropped, pids = ledger.scan_synthetic(raw.decode("utf-8", "replace"))
            if not dropped:
                break
            store.backup(cfg, path)
            if os.path.getsize(path) != len(raw):
                continue
            fs.atomic_write(path, ("\n".join(kept) + "\n" if kept else "").encode(), 0o600)
            done["ledgers"] = int(str(done["ledgers"])) + 1
            done["prompts"] = int(str(done["prompts"])) + len(pids)
            done["records"] = int(str(done["records"])) + dropped
            break
        else:
            busy.append(sid[:8])
    done["busy"] = busy
    return done
