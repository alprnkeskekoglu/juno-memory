import json
import os
from typing import Any

from juno import config
from juno.adapters import cardjob, checks, fs, ledgerio, store
from juno.core import stalerow

NOTIFIED_FILE = "stalefix-notified"
RUN_DAYS = 90


def jobs_dir(cfg: config.Config) -> str:
    return os.path.join(cfg.state, *stalerow.RUNS_DIR)


def runs_path(cfg: config.Config) -> str:
    return os.path.join(jobs_dir(cfg), stalerow.RUNS_FILE)


def read_runs(cfg: config.Config) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in store.read_text(runs_path(cfg)).splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("t"), int | float):
            rows.append(row)
    return rows


def append_run(cfg: config.Config, row: dict[str, Any]) -> None:
    fs.append_log(runs_path(cfg), json.dumps(row, ensure_ascii=False, sort_keys=True), 1 << 26)
    prune_runs(cfg, float(row["t"]))


def prune_runs(cfg: config.Config, now: float) -> None:
    rows = read_runs(cfg)
    kept = [r for r in rows if now - r["t"] <= RUN_DAYS * 86400]
    if len(kept) != len(rows):
        text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in kept)
        fs.atomic_write(runs_path(cfg), text.encode("utf-8"), 0o600)


def last_row(cfg: config.Config, statuses: tuple[str, ...]) -> dict[str, Any] | None:
    found = [r for r in read_runs(cfg) if r.get("status") in statuses]
    return found[-1] if found else None


def week_done(cfg: config.Config, week: str) -> bool:
    return any(r.get("status") == "ok" and r.get("week") == week for r in read_runs(cfg))


def busy(cfg: config.Config, now: float) -> bool:
    window = cfg.get_int("stale", "busy_minutes") * 60
    for sid in ledgerio.ledger_ids(cfg):
        if sid.startswith(checks.JOB_PREFIX):
            continue
        if now - ledgerio.mtime(cfg, sid) < window or cardjob.job_active(cfg, sid, now):
            return True
    return False


def notice(cfg: config.Config) -> str:
    row = last_row(cfg, ("ok", "failed", "refused", "budget"))
    if row is None:
        return ""
    quiet = row["status"] in ("ok", "budget")
    key = str(row["run"]) if quiet else f"{row['status']}:{row.get('reason')}"
    marker = os.path.join(cfg.state, NOTIFIED_FILE)
    if store.read_text(marker).strip() == key:
        return ""
    fs.ensure_private_dir(cfg.state)
    fs.atomic_write(marker, (key + "\n").encode(), 0o600)
    return stalerow.row_line(row)
