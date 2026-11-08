from collections.abc import Mapping, Sequence
from typing import Any

from juno.core import vaultfmt

DAY = 86400
KINDS = ("knowledge", "procedure", "note", "idea")


def report(
    files: Mapping[str, float], rows: Sequence[Mapping[str, Any]], now: float, window_days: int
) -> dict[str, Any]:
    start = min((float(r["t"]) for r in rows), default=None)
    last: dict[str, float] = {}
    for row in rows:
        rel = str(row["rel"])
        last[rel] = max(last.get(rel, 0.0), float(row["t"]))
    cutoff = now - window_days * DAY
    found: list[dict[str, Any]] = []
    if start is not None:
        for rel, created in sorted(files.items()):
            if max(created, start) <= cutoff and last.get(rel, 0.0) < cutoff:
                found.append(
                    {
                        "path": rel,
                        "kind": vaultfmt.kind_of(rel),
                        "created": round(created),
                        "last_signal": round(last[rel]) if rel in last else None,
                    }
                )
    return {
        "window_days": window_days,
        "trace_start": None if start is None else round(start),
        "checked": len(files),
        "candidates": found,
    }


def render(result: Mapping[str, Any]) -> str:
    if result["trace_start"] is None:
        return "No usage trace yet; nothing can decay."
    lines = [
        f"{len(result['candidates'])} of {result['checked']} files without a signal "
        f"in {result['window_days']} days:"
    ]
    for item in result["candidates"]:
        lines.append(f"  {item['path']}")
    return "\n".join(lines)
