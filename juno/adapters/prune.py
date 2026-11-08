import contextlib
import os
import time

from juno import config
from juno.adapters import agentcalls, armsio, asklog, cardjob, queues, shadowio, usageio

DAY = 86400
JOB_DAYS = 8


def remove_older(directory: str, cutoff: float) -> None:
    import shutil

    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        path = os.path.join(directory, name)
        try:
            if os.path.getmtime(path) >= cutoff:
                continue
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.unlink(path)
        except OSError:
            continue


def prune_state(cfg: config.Config, now: float | None = None) -> None:
    now = now or time.time()
    ledger_days = cfg.get_int("retention", "ledger_days")
    state = cfg.state
    remove_older(os.path.join(state, "ledgers"), now - ledger_days * DAY)
    remove_older(
        os.path.join(state, "backups"), now - cfg.get_int("retention", "backup_days") * DAY
    )
    remove_older(os.path.join(state, "status"), now - DAY)
    remove_older(os.path.join(state, "topics"), now - ledger_days * DAY)
    agentcalls.reap(cfg, now)
    with contextlib.suppress(OSError):
        shadowio.prune(state, cfg.get_int("retention", "shadow_days"), now)
    with contextlib.suppress(OSError):
        armsio.prune_segments(cfg, cfg.get_int("retention", "arm_segments_days"), now)
    with contextlib.suppress(OSError):
        asklog.prune(state, cfg.get_int("retention", "asks_days"), now)
    with contextlib.suppress(OSError):
        usageio.prune(state, cfg.get_int("retention", "usage_days"), now)
    prune_cards(cfg, now)


def prune_cards(cfg: config.Config, now: float) -> None:
    cards = cardjob.cards_dir(cfg)
    try:
        names = [n for n in os.listdir(cards) if n.endswith((".job.json", ".lock"))]
    except OSError:
        return
    shared = {q.name + ".lock" for q in (queues.RULES, queues.THREADS, queues.BACKLOG)}
    for name in names:
        path = os.path.join(cards, name)
        try:
            if name in shared or os.path.getmtime(path) >= now - JOB_DAYS * DAY:
                continue
            if name.endswith(".lock") and cardjob.lock_held(path):
                continue
            os.unlink(path)
        except OSError:
            continue
