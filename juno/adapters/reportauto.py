import json
import os
import sys
import time

from juno import config, paths
from juno.adapters import cardjob, fs, store
from juno.core import isoweek

ATTEMPT_FILE = "report-attempt.json"
NOTIFIED_FILE = "report-notified"
RETRY_SECONDS = 6 * 3600
HISTORY = ("shadow.jsonl", "asks.jsonl", "writes.jsonl", os.path.join("cards", "runs.jsonl"))


def report_rel(label: str) -> str:
    return f"_generated/reports/{label}.md"


def has_history(cfg: config.Config) -> bool:
    return any(os.path.exists(os.path.join(cfg.state, name)) for name in HISTORY)


def attempted(cfg: config.Config, label: str, now: float) -> bool:
    data = store.read_json_dict(os.path.join(cfg.state, ATTEMPT_FILE))
    stamp = data.get("t")
    return (
        data.get("week") == label and isinstance(stamp, int | float) and now - stamp < RETRY_SECONDS
    )


def spawn(cfg: config.Config, label: str) -> None:
    import subprocess

    python = cfg.get_str("runtime", "python") or sys.executable
    script = os.path.join(paths.plugin_root(), "juno", "cli.py")
    env = {k: v for k, v in os.environ.items() if k not in cardjob.SESSION_VARS}
    env["JUNO_DISABLE"] = "1"
    fs.ensure_private_dir(cfg.state)
    subprocess.Popen(
        [python, "-I", "-S", script, "report", "--week", label],
        cwd=cfg.state,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def tick(cfg: config.Config, now: float | None = None) -> str:
    now = time.time() if now is None else now
    label = isoweek.last_week(now)
    target = os.path.join(cfg.vault, *report_rel(label).split("/"))
    marker = os.path.join(cfg.state, NOTIFIED_FILE)
    if os.path.isfile(target):
        if store.read_text(marker).strip() == label:
            return ""
        fs.ensure_private_dir(cfg.state)
        fs.atomic_write(marker, (label + "\n").encode(), 0o600)
        return f"Juno: weekly report {label} ready: {report_rel(label)}"
    if has_history(cfg) and not attempted(cfg, label, now):
        fs.ensure_private_dir(cfg.state)
        row = json.dumps({"week": label, "t": now})
        fs.atomic_write(os.path.join(cfg.state, ATTEMPT_FILE), (row + "\n").encode(), 0o600)
        spawn(cfg, label)
    return ""
