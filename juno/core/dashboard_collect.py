import datetime
import importlib
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from juno.adapters import fs
from juno.core import backlog, schedule, stalerow, vaultfmt
from juno.core import dashboard_grade as grade
from juno.core import dashboard_parse as parse
from juno.core import dashboard_scan as scan
from juno.core import dashboard_usage as usage
from juno.core.dashboard_text import Text

CARD_HEADINGS = 8
BACKLOG_LIMIT = 40
CACHE_SECONDS = 3600
REBUILD_SECONDS = 7200
FILE_LIMIT = 1048576
ERROR_TAIL = 5
MISSES_KEPT = 5
PLAIN_STATUSES = ("planned", "in-progress")
QUOTA_KEYS = ("five_hour", "seven_day")
WRITE_KINDS = ("card", "knowledge", "decision", "pitfall", "rule", "fix")
WEEKS = 8
STALE_CACHE_VERSION = 1
SKIP_TOP = {".git", ".obsidian", "archive", "_generated"}

Block = dict[str, Any]


@dataclass
class Sources:
    vault: str
    state: str
    transcripts: str
    home: str
    t: Text
    ticket: re.Pattern[str] | None = None
    daily_budget: float | None = None
    arm_rows: Callable[[], list[dict[str, Any]]] | None = None


def unavailable(exc: BaseException) -> Block:
    first = str(exc).strip().splitlines()[:1]
    detail = first[0][:160] if first else ""
    return {"unavailable": f"{type(exc).__name__}{': ' + detail if detail else ''}"}


def guarded(func: Callable[..., Any], *args: Any) -> Any:
    try:
        return func(*args)
    except Exception as exc:
        return unavailable(exc)


def num(value: object) -> float:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value) if ok and isinstance(value, (int, float)) else 0.0


def read_text(path: str, limit: int = FILE_LIMIT * 8) -> str:
    if os.path.getsize(path) > limit:
        raise ValueError(f"{os.path.basename(path)} is over {limit} bytes")
    raw = fs.read_bytes(path)
    if raw is None:
        raise FileNotFoundError(f"{os.path.basename(path)} not found")
    return raw.decode("utf-8", "replace")


def read_json(path: str, label: str) -> Any:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{label} not found")
    return json.loads(read_text(path, FILE_LIMIT))


def read_jsonl(path: str) -> tuple[list[Block], int]:
    rows: list[Block] = []
    bad = 0
    raw = fs.read_bytes(path)
    for line in (raw or b"").decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            bad += 1
    return rows, bad


def md_files(directory: str) -> list[str]:
    if os.path.islink(directory) or not os.path.isdir(directory):
        raise FileNotFoundError(f"{os.path.basename(directory)}/ not found")
    return sorted(n for n in os.listdir(directory) if n.endswith(".md") and not n.startswith("."))


def today_threads(src: Sources, now: float) -> Block:
    path = os.path.join(src.vault, "threads.md")
    if not os.path.isfile(path):
        raise FileNotFoundError("threads.md not found")
    return {"items": parse.parse_threads(read_text(path), usage.local_day(now), src.ticket)}


def today_backlog(src: Sources, now: float) -> Block:
    directory = os.path.join(src.vault, "backlog")
    files = [
        (n[:-3], read_text(os.path.join(directory, n), FILE_LIMIT)) for n in md_files(directory)
    ]
    return parse.backlog_block(files, usage.local_day(now), BACKLOG_LIMIT)


def today_handoffs(src: Sources) -> Block:
    directory = os.path.join(src.vault, "handoffs")
    cards: list[Block] = []
    for name in md_files(directory):
        cards += parse.parse_cards(read_text(os.path.join(directory, name)), f"handoffs/{name}")
    return parse.cards_block(cards, CARD_HEADINGS)


def collect_today(src: Sources, now: float) -> Block:
    return {
        "threads": guarded(today_threads, src, now),
        "backlog": guarded(today_backlog, src, now),
        "handoffs": guarded(today_handoffs, src),
    }


def cost_usage(src: Sources, now: float) -> Block:
    prices = scan.load_prices(src.state)
    if not os.path.isdir(src.transcripts):
        raise FileNotFoundError(f"transcripts dir not found: {src.transcripts}")
    cache, stats = scan.collect(src.state, src.transcripts, now)
    scan.save_cache(src.state, cache)
    result = usage.aggregate(cache, prices, now, src.home)
    result.update(stats=stats, prices_source=prices.get("source"))
    return result


def find_quota(node: object, depth: int = 0) -> Block:
    if depth > 4 or not isinstance(node, (dict, list)):
        return {}
    found: Block = {}
    items = node.items() if isinstance(node, dict) else enumerate(node)
    for key, value in items:
        if key in QUOTA_KEYS:
            found[str(key)] = value
        elif isinstance(value, (dict, list)):
            for k, v in find_quota(value, depth + 1).items():
                found.setdefault(k, v)
    return found


def cost_quota(src: Sources) -> Block:
    data = read_json(os.path.join(src.state, "status.json"), "status.json")
    found = find_quota(data)
    if not found:
        raise KeyError("status.json has no usage fields (5h and weekly usage are not recorded)")
    return {"windows": found, "updated": data.get("updated") if isinstance(data, dict) else None}


def epoch_of(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None
    return None


def spend_by_day(rows: list[Block], days: list[str]) -> dict[str, float]:
    out = dict.fromkeys(days, 0.0)
    for row in rows:
        epoch = epoch_of(row.get("t"))
        if epoch is None:
            continue
        day = usage.local_day(epoch)
        if day in out:
            out[day] += num(row.get("cost_usd"))
    return {d: round(v, 4) for d, v in out.items()}


def cost_juno(src: Sources, now: float) -> Block:
    days = usage.window_days(usage.LOOKBACK_DAYS, now)
    runs, bad = read_jsonl(os.path.join(src.state, "cards", "runs.jsonl"))
    return {
        "days": days,
        "cards": {"spend": spend_by_day(runs, days), "budget": src.daily_budget, "bad_lines": bad},
    }


def collect_cost(src: Sources, now: float) -> Block:
    return {
        "usage": guarded(cost_usage, src, now),
        "quota": guarded(cost_quota, src),
        "juno": guarded(cost_juno, src, now),
    }


def write_records(state: str) -> tuple[list[Block], int]:
    rows: list[Block] = []
    bad = 0
    for name in ("writes.jsonl.1", "writes.jsonl"):
        path = os.path.join(state, name)
        if os.path.isfile(path):
            found, skipped = read_jsonl(path)
            rows += found
            bad += skipped
    return rows, bad


def collect_writes(src: Sources, now: float) -> Block:
    days = usage.window_days(usage.LOOKBACK_DAYS, now)
    rows, bad = write_records(src.state)
    undone = {str(r.get("of")) for r in rows if r.get("verb") == "undo"}
    kinds = (*WRITE_KINDS, "other")
    by_day = {d: dict.fromkeys(kinds, 0) for d in days}
    for row in rows:
        epoch = epoch_of(row.get("t"))
        verb = row.get("verb")
        if epoch is None or verb == "undo" or str(row.get("id")) in undone:
            continue
        day = usage.local_day(epoch)
        if day in by_day:
            by_day[day][verb if verb in WRITE_KINDS else "other"] += 1
    totals = [sum(by_day[d].values()) for d in days]
    return {
        "days": days,
        "kinds": list(kinds),
        "by_day": by_day,
        "today": totals[-1],
        "week": sum(totals[-usage.WINDOW_DAYS :]),
        "month": sum(totals),
        "bad_lines": bad,
    }


def collect_arms(src: Sources) -> Block:
    if src.arm_rows is None:
        return {"items": []}
    return {"items": src.arm_rows()}


def health_runs(src: Sources, now: float) -> Block:
    days = usage.window_days(usage.WINDOW_DAYS, now)
    rows, bad = read_jsonl(os.path.join(src.state, "cards", "runs.jsonl"))
    counts = {d: {"ok": 0, "skipped": 0, "failed": 0, "other": 0} for d in days}
    errors = []
    for row in rows:
        epoch = epoch_of(row.get("t"))
        day = usage.local_day(epoch) if epoch is not None else None
        if day is None or day not in counts:
            continue
        raw = row.get("status")
        status = raw if raw in ("ok", "skipped", "failed") else "other"
        counts[day][status] += 1
        if status == "failed":
            errors.append({"day": day, "error": str(row.get("error") or "no error recorded")[:200]})
    return {
        "days": [dict(day=d, **counts[d]) for d in days],
        "errors": errors[-ERROR_TAIL:],
        "bad_lines": bad,
    }


def error_times(lines: list[str]) -> list[float]:
    found = []
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            found.append(time.mktime(time.strptime(parts[0][:19], "%Y-%m-%dT%H:%M:%S")))
        except ValueError:
            continue
    return found


def health_hook_errors(src: Sources, now: float) -> Block:
    path = os.path.join(src.state, "hook-errors.log")
    raw = fs.read_bytes(path)
    lines = (raw or b"").decode("utf-8", "replace").splitlines()
    recent = sum(1 for ts in error_times(lines) if 0 <= now - ts <= 86400)
    return {
        "count": len(lines),
        "last_24h": recent,
        "exists": raw is not None,
        "path": path,
        "last": [line[:240] for line in lines[-ERROR_TAIL:]],
    }


def pending_count(path: str) -> int:
    data = read_json(path, os.path.basename(path))
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return 0
    return sum(1 for i in items if isinstance(i, dict) and i.get("status") == "pending")


def health_queues(src: Sources) -> Block:
    def count(name: str) -> Any:
        path = os.path.join(src.state, "cards", name)
        return guarded(pending_count, path) if os.path.isfile(path) else 0

    return {
        "rule_candidates": count("candidates.json"),
        "thread_candidates": count("thread-candidates.json"),
        "backlog_suggestions": count("backlog-suggestions.json"),
    }


def vault_markdown(vault: str) -> int:
    total = 0
    for _base, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in SKIP_TOP and not d.startswith(".")]
        total += sum(1 for f in files if f.endswith(".md"))
    return total


def coverage_index(src: Sources) -> Block:
    import sqlite3

    db = os.path.join(src.state, "index.sqlite3")
    if not os.path.isfile(db):
        raise FileNotFoundError("index.sqlite3 not found (built by juno search)")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        try:
            indexed = conn.execute("SELECT COUNT(*) FROM files WHERE archive = 0").fetchone()[0]
        except sqlite3.OperationalError:
            indexed = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    finally:
        conn.close()
    return {"indexed": indexed, "vault": vault_markdown(src.vault)}


def coverage_stale(src: Sources) -> Block:
    path = os.path.join(src.state, "stale.json")
    if not os.path.isfile(path):
        raise FileNotFoundError("stale.json not found (run `juno stale`)")
    try:
        cache = json.loads(read_text(path, FILE_LIMIT))
    except ValueError:
        cache = None
    ok = isinstance(cache, dict) and cache.get("v") == STALE_CACHE_VERSION
    if not ok or not isinstance(cache.get("files"), dict):
        raise ValueError("stale.json is unreadable or from another version")
    names = set()
    for folder in ("knowledge", "projects"):
        try:
            names |= {f"{folder}/{n}" for n in md_files(os.path.join(src.vault, folder))}
        except FileNotFoundError:
            continue
    flagged = [
        k
        for k, v in cache["files"].items()
        if k in names and isinstance(v, dict) and v.get("stale")
    ]
    return {"stale": len(flagged), "total": len(names), "generated": cache.get("generated")}


def schedule_installed(src: Sources) -> bool:
    agent = os.path.join(src.home, "Library", "LaunchAgents", f"{schedule.LABEL}.plist")
    unit = os.path.join(src.home, ".config", "systemd", "user", f"{schedule.UNIT}.timer")
    return os.path.exists(agent) or os.path.exists(unit)


def schedule_installed_at(src: Sources) -> float | None:
    agent = os.path.join(src.home, "Library", "LaunchAgents", f"{schedule.LABEL}.plist")
    unit = os.path.join(src.home, ".config", "systemd", "user", f"{schedule.UNIT}.timer")
    for path in (agent, unit):
        try:
            return os.path.getmtime(path)
        except OSError:
            continue
    return None


def coverage_stalefix(src: Sources, now: float) -> Block:
    rows, _ = read_jsonl(os.path.join(src.state, *stalerow.RUNS_DIR, stalerow.RUNS_FILE))
    stamped = [r for r in rows if isinstance(r.get("t"), (int, float))]
    seen = [r for r in stamped if r.get("status") in ("ok", "failed", "refused", "budget")]
    last_ok = [r for r in stamped if r.get("status") == "ok"]
    installed = schedule_installed(src)
    since = last_ok[-1]["t"] if last_ok else schedule_installed_at(src)
    overdue = installed and since is not None and now - since > stalerow.OVERDUE_DAYS * 86400
    keep = ("day", "status", "fixed", "archived", "unsure", "reason", "run")
    return {
        "last": {k: seen[-1].get(k) for k in keep} if seen else None,
        "installed": installed,
        "overdue": overdue,
    }


def with_fixer(stale_block: Block, fixer: Block) -> Block:
    if "unavailable" in stale_block or "unavailable" in fixer:
        return stale_block
    return dict(stale_block, fixer=fixer)


def collect_health(src: Sources, now: float) -> Block:
    return {
        "runs": guarded(health_runs, src, now),
        "hook_errors": guarded(health_hook_errors, src, now),
        "queues": guarded(health_queues, src),
        "coverage": {
            "index": guarded(coverage_index, src),
            "stale": with_fixer(guarded(coverage_stale, src), guarded(coverage_stalefix, src, now)),
        },
    }


def cached_result(src: Sources, name: str, now: float, compute: Callable[[], Block]) -> Block:
    path = scan.dashboard_dir(src.state, name)
    try:
        cached = read_json(path, name)
        if isinstance(cached.get("t"), (int, float)) and 0 <= now - cached["t"] < CACHE_SECONDS:
            return dict(cached["result"], cached_at=cached["t"])
    except OSError, ValueError, AttributeError, TypeError:
        pass
    result = compute()
    scan.write_json(path, {"t": now, "result": result})
    return dict(result, cached_at=now)


def eval_module() -> Any:
    try:
        return importlib.import_module("juno.adapters.evalio")
    except ImportError as error:
        raise FileNotFoundError("eval module not installed") from error


def experiments_ab(src: Sources, now: float) -> Block:
    def compute() -> Block:
        result: Block = eval_module().card_ab(src.state, src.vault, src.transcripts)
        return result

    return cached_result(src, "card-ab.json", now, compute)


def experiments_recall(src: Sources, now: float) -> Block:
    def compute() -> Block:
        result: Block = eval_module().recall_proxy(src.state, src.vault, src.transcripts)
        return dict(result, misses=result["misses"][:MISSES_KEPT])

    return cached_result(src, "recall.json", now, compute)


def experiments_recall_weekly(src: Sources, now: float) -> Block:
    def compute() -> Block:
        weeks: list[Block] = eval_module().recall_weekly(
            src.state, src.vault, src.transcripts, now, WEEKS
        )
        return {"weeks": weeks}

    return cached_result(src, "recall-weekly.json", now, compute)


def experiments_gate(src: Sources) -> Block:
    directory = os.path.join(src.state, "eval")
    sets = []
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        if re.match(r"^set-\d+\.json$", name):
            sets.append(
                {"name": name, "frozen": os.path.isfile(os.path.join(directory, name + ".sha256"))}
            )
    last = guarded(
        read_json,
        os.path.join(directory, "last-gate.json"),
        "eval/last-gate.json (run `juno eval gate`)",
    )
    return {"last": last, "sets": sets}


def experiments_tasks(src: Sources) -> Block:
    directory = os.path.join(src.vault, "tasks")
    rows = []
    for name in md_files(directory):
        fields, _ = vaultfmt.parse_frontmatter(read_text(os.path.join(directory, name), FILE_LIMIT))
        status = backlog.as_text(fields.get("status")).strip().casefold()
        if status not in vaultfmt.FINISHED_STATUSES:
            rows.append(
                {
                    "key": name[:-3],
                    "status": status or "-",
                    "title": backlog.as_text(fields.get("title")),
                    "repos": backlog.as_text(fields.get("repos") or fields.get("repo")),
                }
            )
    return {"items": rows}


def experiments_ideas(src: Sources) -> Block:
    directory = os.path.join(src.vault, "ideas")
    rows = []
    for name in md_files(directory):
        fields, body = vaultfmt.parse_frontmatter(
            read_text(os.path.join(directory, name), FILE_LIMIT)
        )
        status = backlog.as_text(fields.get("status")).strip().casefold()
        if status in PLAIN_STATUSES:
            rows.append(
                {"id": name[:-3], "status": status, "title": parse.idea_title(body, name[:-3])}
            )
    return {"items": rows}


def collect_experiments(src: Sources, now: float) -> Block:
    return {
        "card_ab": guarded(experiments_ab, src, now),
        "recall": guarded(experiments_recall, src, now),
        "recall_weekly": guarded(experiments_recall_weekly, src, now),
        "gate": guarded(experiments_gate, src),
        "tasks": guarded(experiments_tasks, src),
        "ideas": guarded(experiments_ideas, src),
    }


def collect(src: Sources, now: float) -> Block:
    data: Block = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(now)),
        "generated_epoch": now,
        "next_rebuild_epoch": now + REBUILD_SECONDS,
        "today_date": usage.local_day(now),
        "language": src.t.lang,
        "today": collect_today(src, now),
        "cost": collect_cost(src, now),
        "health": collect_health(src, now),
        "experiments": collect_experiments(src, now),
        "writes": guarded(collect_writes, src, now),
        "arms": guarded(collect_arms, src),
    }
    rows = grade.grade(data, src.t)
    data["health"]["rows"] = rows
    data["health"]["verdict"] = grade.verdict(rows)
    return data
