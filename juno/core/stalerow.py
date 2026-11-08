import datetime
from typing import Any

JOB = "stale-weekly"
ACTOR = f"job:{JOB}"
RUNS_DIR = ("jobs", JOB)
RUNS_FILE = "runs.jsonl"
OVERDUE_DAYS = 8


def week_of(day: datetime.date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def row_line(row: dict[str, Any]) -> str:
    day = str(row.get("day", ""))
    status = row.get("status")
    if status in ("failed", "refused"):
        return f"Juno: stale fixer {day} {status}: {str(row.get('reason', ''))[:120]}"
    return (
        f"Juno: stale fixer {day}: {row.get('fixed', 0)} lines fixed, {row.get('archived', 0)} "
        f"archived, {row.get('unsure', 0)} unsure (juno undo --run {row.get('run', '')}; "
        "juno stale for the rest)"
    )
