import glob
import json
import os
import stat
import time
from typing import Any

from juno import config, paths
from juno.adapters import fs, ledgerio, store
from juno.core import ledger, privacy
from juno.core.ledger import Segment

SESSION_VARS = (
    "CLAUDECODE",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_PID",
    "CLAUDE_PROJECT_DIR",
    "CLAUDE_ENV_FILE",
    "CLAUDE_CODE_SSE_PORT",
)
CARD_PENDING_SECONDS = 120
SESSION_END_DEADLINE = 1.0
RUNS_FILE = "runs.jsonl"


def cards_dir(cfg: config.Config) -> str:
    return os.path.join(cfg.state, "cards")


def job_paths(cfg: config.Config, sid: object) -> tuple[str, str] | None:
    if not ledger.safe_id(sid):
        return None
    directory = cards_dir(cfg)
    return os.path.join(directory, f"{sid}.job.json"), os.path.join(directory, f"{sid}.lock")


def read_job(cfg: config.Config, sid: object) -> dict[str, Any]:
    found = job_paths(cfg, sid)
    return dict(store.read_json_dict(found[0])) if found else {}


def write_job(cfg: config.Config, sid: str, job: dict[str, Any]) -> None:
    found = job_paths(cfg, sid)
    if found is None:
        return
    fs.ensure_private_dir(cards_dir(cfg))
    fs.atomic_write(
        found[0], (json.dumps(job, ensure_ascii=False, sort_keys=True) + "\n").encode(), 0o600
    )


def lock_held(path: str) -> bool:
    import fcntl

    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def job_active(cfg: config.Config, sid: object, now: float | None = None) -> bool:
    found = job_paths(cfg, sid)
    if found is None:
        return False
    job = read_job(cfg, sid)
    fresh = (now or time.time()) - number(job.get("t"), 0.0) < CARD_PENDING_SECONDS
    if job.get("status") == "pending" and fresh:
        return True
    return job.get("status") in ("pending", "running") and lock_held(found[1])


def read_runs(cfg: config.Config) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in store.read_text(os.path.join(cards_dir(cfg), RUNS_FILE)).splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("t"), (int, float)):
            rows.append(row)
    return rows


def number(value: object, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def spent_today(cfg: config.Config, now: float | None = None) -> float:
    today = time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))
    return sum(
        number(r.get("cost_usd"), 0.0)
        for r in read_runs(cfg)
        if time.strftime("%Y-%m-%d", time.localtime(r["t"])) == today
    )


def run_budget(cfg: config.Config, now: float | None = None) -> float:
    left = cfg.get_float("handoff", "daily_budget_usd") - spent_today(cfg, now)
    return round(min(cfg.get_float("handoff", "budget_usd"), left), 2)


def skip_reason(cfg: config.Config, seg: Segment) -> str | None:
    if not seg.meaningful:
        return "not meaningful"
    if not seg.dirty:
        return "card current"
    if seg.private:
        return "private"
    if seg.nosave:
        return "nosave"
    if job_active(cfg, seg.sid):
        return "running"
    if run_budget(cfg) < cfg.get_float("handoff", "min_run_usd"):
        return "budget"
    return None


def needs_stub(seg: Segment) -> bool:
    return seg.meaningful and seg.dirty and not seg.nosave and not seg.private


def child_env(cfg: config.Config, token: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SESSION_VARS}
    home = env.get("HOME") or os.path.expanduser("~")
    launch = cfg.get_str("handoff", "launch_path")
    env.update(JUNO_DISABLE="1", JUNO_CARD_TOKEN=token, PATH=launch.replace("~", home))
    return env


def spawn(cfg: config.Config, sid: str, token: str) -> None:
    import subprocess
    import sys

    python = cfg.get_str("runtime", "python") or sys.executable
    script = os.path.join(paths.plugin_root(), "juno", "hooks", "card_run.py")
    directory = cards_dir(cfg)
    fs.ensure_private_dir(directory)
    subprocess.Popen(
        [python, "-I", "-S", script, "run", sid],
        cwd=directory,
        env=child_env(cfg, token),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def start_run(cfg: config.Config, sid: str, reason: str, transcript: str | None) -> str:
    token = os.urandom(16).hex()
    job = {
        "sid": sid,
        "status": "pending",
        "t": ledgerio.stamp(),
        "token": token,
        "reason": reason,
        "transcript": transcript,
        "max_budget_usd": run_budget(cfg),
    }
    write_job(cfg, sid, job)
    spawn(cfg, sid, token)
    return token


def on_session_end(
    cfg: config.Config, payload: dict[str, Any], started: float, now: float | None = None
) -> str | None:
    sid = payload.get("session_id")
    if ledgerio.ledger_path(cfg, sid) is None:
        return "bad session id"
    assert isinstance(sid, str)
    reason = str(payload.get("reason"))[:20]
    skip = skip_reason(cfg, ledgerio.fold(cfg, sid))
    if skip is None and (now or time.monotonic()) - started > SESSION_END_DEADLINE:
        skip = "deadline"
    ledgerio.append(cfg, sid, {"ev": "end", "reason": reason, "skip": skip})
    if skip:
        return skip
    transcript = payload.get("transcript_path")
    start_run(cfg, sid, reason, transcript if isinstance(transcript, str) else None)
    return None


def projects_root(cfg: config.Config) -> str:
    override = cfg.env.get("JUNO_CLAUDE_PROJECTS", "")
    return os.path.realpath(
        paths.expand(override, cfg.env) if override else os.path.join(cfg.claude_dir, "projects")
    )


def safe_transcript(path: str, root: str) -> bool:
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return False
        return os.path.realpath(path).startswith(root + os.sep)
    except OSError, TypeError, ValueError:
        return False


def find_transcript(cfg: config.Config, seg: Segment) -> str | None:
    root = projects_root(cfg)
    if isinstance(seg.transcript, str) and safe_transcript(seg.transcript, root):
        return seg.transcript
    pattern = os.path.join(glob.escape(root), "*", seg.sid + ".jsonl")
    for path in sorted(glob.glob(pattern))[:5]:
        if safe_transcript(path, root):
            return path
    return None


def already_ran(cfg: config.Config, seg: Segment) -> bool:
    stamp = read_job(cfg, seg.sid).get("t")
    return isinstance(stamp, (int, float)) and stamp >= (seg.last_t or 0)


def late_transcript(cfg: config.Config, seg: Segment) -> str | None:
    if cfg.get_str("handoff", "mode") != "end" or skip_reason(cfg, seg) or already_ran(cfg, seg):
        return None
    return find_transcript(cfg, seg)


def late_run(cfg: config.Config, seg: Segment) -> bool:
    transcript = late_transcript(cfg, seg)
    if not transcript:
        return False
    start_run(cfg, seg.sid, "late", transcript)
    return True


def card_failure(cfg: config.Config, sid: object) -> str | None:
    """Short, privacy-safe reason the last card job rejected its card, or None."""
    job = read_job(cfg, sid)
    error = " ".join(str(job.get("error") or "").split())
    if job.get("status") != "failed" or not error.startswith("invalid:"):
        return None
    return None if privacy.scan(error) else error[:80]
