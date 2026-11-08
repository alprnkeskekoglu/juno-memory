import shlex
from collections.abc import Callable
from typing import Any

from juno.core.dashboard_fmt import is_na
from juno.core.dashboard_text import Text

HOOK_RED = 5
INDEX_YELLOW = 0.98
INDEX_RED = 0.75
QUEUES = (
    ("backlog_suggestions", "q_backlog_n", "juno backlog list"),
    ("thread_candidates", "q_thread_n", "juno thread pending"),
    ("rule_candidates", "q_rule_n", "juno rule pending"),
)

Row = dict[str, Any]


def row(rid: str, label: str, level: str, detail: str, fix: str | None = None) -> Row:
    return {
        "id": rid,
        "label": label,
        "level": level,
        "detail": detail,
        "fix": fix if level in ("yellow", "red") else None,
    }


def na_row(rid: str, label: str, block: dict[str, Any], t: Text) -> Row:
    return row(rid, label, "na", t("unavailable", block["unavailable"]))


def grade_runs(block: dict[str, Any], t: Text) -> Row:
    today = block["days"][-1]["failed"]
    failed = sum(d["failed"] for d in block["days"])
    level = "red" if today else "yellow" if failed else "green"
    return row("runs", t("g_runs"), level, t("g_runs_detail", today, failed), "juno doctor")


def grade_hooks(block: dict[str, Any], t: Text) -> Row:
    count = block["last_24h"]
    level = "red" if count >= HOOK_RED else "yellow" if count else "green"
    fix = "tail -n 20 " + shlex.quote(block["path"])
    return row("hook_errors", t("g_hooks"), level, t("g_hooks_detail", count), fix)


def grade_queues(block: dict[str, Any], t: Text) -> Row:
    parts, fixes, bad = [], [], []
    for key, label, command in QUEUES:
        value = block[key]
        if is_na(value):
            bad.append(value["unavailable"])
        elif value:
            parts.append(f"{value} {t(label)}")
            fixes.append(command)
    if bad and len(bad) == len(QUEUES):
        return na_row("queues", t("g_queues"), {"unavailable": bad[0]}, t)
    detail = ", ".join(parts) or t("g_queues_empty")
    return row("queues", t("g_queues"), "yellow" if parts else "green", detail, " ; ".join(fixes))


def grade_stale(block: dict[str, Any], t: Text) -> Row:
    detail = t("g_stale_detail", block["stale"], block["total"])
    return row("stale", t("g_stale"), "yellow" if block["stale"] else "green", detail, "juno stale")


def grade_index(block: dict[str, Any], t: Text) -> Row:
    ratio = block["indexed"] / block["vault"] if block["vault"] else 1.0
    level = "red" if ratio < INDEX_RED else "yellow" if ratio < INDEX_YELLOW else "green"
    detail = t("g_index_detail", block["indexed"], block["vault"])
    return row("index", t("g_index"), level, detail, 'juno search "index"')


Builder = Callable[[dict[str, Any], Text], Row]


def grade_one(rid: str, label: str, block: Any, build: Builder, t: Text) -> Row:
    if is_na(block):
        return na_row(rid, label, block, t)
    try:
        return build(block, t)
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        return na_row(rid, label, {"unavailable": f"{type(exc).__name__}: {exc}"}, t)


def grade(data: dict[str, Any], t: Text) -> list[Row]:
    health = data["health"]
    missing = {"unavailable": "coverage missing"}
    coverage = health.get("coverage", missing)
    rows = [
        grade_one(key, t(label), health.get(key, {"unavailable": f"{key} missing"}), build, t)
        for key, label, build in (
            ("runs", "g_runs", grade_runs),
            ("hook_errors", "g_hooks", grade_hooks),
            ("queues", "g_queues", grade_queues),
        )
    ]
    for key, label, build in (("stale", "g_stale", grade_stale), ("index", "g_index", grade_index)):
        block = (
            coverage if is_na(coverage) else coverage.get(key, {"unavailable": f"{key} missing"})
        )
        rows.append(grade_one(key, t(label), block, build, t))
    return rows


def verdict(rows: list[Row]) -> dict[str, Any]:
    red = sum(r["level"] == "red" for r in rows)
    yellow = sum(r["level"] == "yellow" for r in rows)
    level = "bad" if red else "warn" if yellow else "good"
    return {
        "level": level,
        "red": red,
        "yellow": yellow,
        "na": sum(r["level"] == "na" for r in rows),
    }
