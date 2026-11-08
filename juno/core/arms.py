import hashlib
import json
import re
from collections.abc import Collection, Mapping
from typing import Any

MAX_RUNNING = 2
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
DAY = 86400
TEXT_KEYS = ("hypothesis", "unit", "stop_rule", "decision")
OPTIONAL_TEXT_KEYS = ("baseline", "caveat", "note")


def assign(salt: str, sid: str, fraction: float) -> str:
    bucket = int(hashlib.sha256((salt + sid).encode("utf-8")).hexdigest()[:8], 16) / 2**32
    return "on" if bucket < fraction else "off"


def is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate(prereg: object, codes: Collection[str] | None = None) -> list[str]:
    if not isinstance(prereg, dict):
        return ["the pre-registration must be a JSON object"]
    problems = [
        f"{key} must be non-empty text" for key in TEXT_KEYS if not is_text(prereg.get(key))
    ]
    problems += [
        f"{key} must be text when present"
        for key in OPTIONAL_TEXT_KEYS
        if key in prereg and not isinstance(prereg[key], str)
    ]
    assignment = prereg.get("assignment")
    fraction = assignment.get("fraction") if isinstance(assignment, dict) else None
    if (
        not isinstance(fraction, (int, float))
        or isinstance(fraction, bool)
        or not 0 <= fraction <= 1
    ):
        problems.append("assignment.fraction must be a number from 0 to 1")
    metric = prereg.get("metric")
    if not isinstance(metric, dict) or not is_text(metric.get("primary")):
        problems.append("metric.primary must be non-empty text")
    code = metric.get("code") if isinstance(metric, dict) else None
    if not is_text(code):
        problems.append("metric.code must name a registered metric")
    elif codes is not None and code not in codes:
        problems.append(
            f"metric.code {code!r} is not registered (known: {', '.join(sorted(codes))})"
        )
    for key in ("min_n", "max_days"):
        value = prereg.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            problems.append(f"{key} must be an integer of at least 1")
    return problems


def body_hash(prereg: Mapping[str, Any]) -> str:
    raw = json.dumps(prereg, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def decide_refusal(
    prereg: Mapping[str, Any], started: float, now: float, outcome: Mapping[str, Any]
) -> str | None:
    counts = outcome.get("n")
    counts = counts if isinstance(counts, dict) else {}
    smallest = min(int(counts.get("on", 0)), int(counts.get("off", 0)))
    ready = outcome.get("ready", True)
    if (smallest >= prereg["min_n"] and ready) or outcome.get("stop_fired"):
        return None
    if now - started >= prereg["max_days"] * DAY:
        return None
    why = f" ({outcome['reason']})" if ready is False and outcome.get("reason") else ""
    return (
        f"min_n {prereg['min_n']} per arm not reached ({counts.get('on', 0)} on, "
        f"{counts.get('off', 0)} off){why}, the stop rule has not fired and max_days "
        f"{prereg['max_days']} has not passed"
    )
