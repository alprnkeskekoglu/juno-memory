import calendar
import datetime
import re
import time
from typing import Any

LOOKBACK_DAYS = 30
WINDOW_DAYS = 7
CONTEXT_LIMIT = 200000
SUFFIX_RE = re.compile(r"(\[[^\]]*\]|-\d{8})$")
SYNTHETIC = "<synthetic>"

Prices = dict[str, Any]
Row = list[int]


def local_day(epoch: float) -> str:
    return datetime.date.fromtimestamp(epoch).isoformat()


def window_days(count: int, now: float) -> list[str]:
    today = datetime.date.fromtimestamp(now)
    return [(today - datetime.timedelta(days=i)).isoformat() for i in range(count - 1, -1, -1)]


def day_start(day: str) -> float:
    return time.mktime(datetime.date.fromisoformat(day).timetuple())


def iso_epoch(text: str) -> float:
    return float(calendar.timegm(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")))


def match_price(prices: Prices, model: object) -> dict[str, Any] | None:
    models, name = prices["models"], str(model or "")
    while True:
        if name in models:
            found: dict[str, Any] = models[name]
            return found
        stripped = SUFFIX_RE.sub("", name)
        if stripped == name:
            return None
        name = stripped


def call_cost(prices: Prices, entry: dict[str, Any], call: tuple[int, int, int, int, int]) -> float:
    """USD for one call; `call` is (input, output, cache_read, cache_5m, cache_1h)."""
    tokens_in, tokens_out, read, write5, write1 = call
    rates = dict(entry)
    tier = rates.pop("above", None)
    if isinstance(tier, dict) and tokens_in + read + write5 + write1 > tier.get(
        "prompt_tokens", float("inf")
    ):
        rates.pop("cache_read", None)
        rates.update({k: v for k, v in tier.items() if k != "prompt_tokens"})
    defaults = prices.get("defaults") or {}
    price_in = rates["in"]
    cache_read = rates.get("cache_read", price_in * defaults.get("cache_read", 0.1))
    return float(
        (
            tokens_in * price_in
            + tokens_out * rates["out"]
            + read * cache_read
            + write5 * price_in * defaults.get("cache_write_5m", 1.25)
            + write1 * price_in * defaults.get("cache_write_1h", 2.0)
        )
        / 1e6
    )


class Interner:
    def __init__(self, strs: list[str]) -> None:
        self.strs = strs
        self.index = {s: i for i, s in enumerate(strs)}

    def __call__(self, text: object) -> int:
        key = str(text or "")
        if key not in self.index:
            self.index[key] = len(self.strs)
            self.strs.append(key)
        return self.index[key]


def count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def usage_row(
    obj: dict[str, Any], path: str, intern: Interner, floor: float
) -> tuple[str, list[int]] | None:
    message = obj.get("message")
    if obj.get("type") != "assistant" or not isinstance(message, dict):
        return None
    usage = message.get("usage")
    mid = message.get("id")
    if not isinstance(usage, dict) or not mid or not isinstance(obj.get("timestamp"), str):
        return None
    epoch = iso_epoch(obj["timestamp"])
    if epoch < floor:
        return None
    created = usage.get("cache_creation")
    if isinstance(created, dict):
        write5 = count(created.get("ephemeral_5m_input_tokens"))
        write1 = count(created.get("ephemeral_1h_input_tokens"))
    else:
        write5, write1 = count(usage.get("cache_creation_input_tokens")), 0
    counts = [
        count(usage.get("input_tokens")),
        count(usage.get("output_tokens")),
        count(usage.get("cache_read_input_tokens")),
        write5,
        write1,
    ]
    if message.get("model") == SYNTHETIC and not any(counts):
        return None
    sub = int("/subagents/" in path or obj.get("isSidechain") is True)
    sid = obj.get("sessionId") or path.rsplit("/", 1)[-1][:-6]
    row = [
        int(epoch),
        intern(message.get("model")),
        *counts,
        intern(sid),
        sub,
        intern(obj.get("cwd")),
    ]
    return str(mid), row


def plan_start(prev: object, size: int, mtime: int) -> int | None:
    """Byte offset to read from, or None when the file is unchanged; 0 rescans from the start."""
    if not isinstance(prev, dict):
        return 0
    old_size, old_mtime = prev.get("size", 0), prev.get("mtime_ns", 0)
    if size < old_size or mtime < old_mtime or prev.get("offset", 0) > size:
        return 0
    if size == old_size:
        return None if mtime == old_mtime else 0
    offset = prev.get("offset", 0)
    return offset if isinstance(offset, int) else 0


def tally(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "calls": len(rows),
        "cost": round(sum(r["cost"] for r in rows), 6),
        "output_tokens": sum(r["out"] for r in rows),
        "context_tokens": sum(r["ctx"] for r in rows),
    }


def project_name(cwd: str, home: str) -> str:
    if home and (cwd == home or cwd.startswith(home + "/")):
        return "~" + cwd[len(home) :]
    return cwd or "unknown"


def aggregate(cache: dict[str, Any], prices: Prices, now: float, home: str) -> dict[str, Any]:
    strs, days = cache["strs"], window_days(LOOKBACK_DAYS, now)
    recent = set(days[-WINDOW_DAYS:])
    rows: list[dict[str, Any]] = []
    unpriced: dict[str, dict[str, int]] = {}
    for epoch, model, tin, tout, read, write5, write1, sid, sub, cwd in cache["msgs"].values():
        day = local_day(epoch)
        if day not in days:
            continue
        ctx = tin + read + write5 + write1
        entry = match_price(prices, strs[model])
        cost = call_cost(prices, entry, (tin, tout, read, write5, write1)) if entry else None
        if entry is None:
            slot = unpriced.setdefault(strs[model] or "unknown", {"calls": 0, "tokens": 0})
            slot["calls"] += 1
            slot["tokens"] += ctx + tout
        rows.append(
            {
                "day": day,
                "model": strs[model],
                "cost": cost or 0.0,
                "priced": entry is not None,
                "out": tout,
                "ctx": ctx,
                "sid": strs[sid],
                "sub": sub,
                "cwd": project_name(strs[cwd], home),
            }
        )
    priced = [r for r in rows if r["priced"]]
    series = [dict(day=d, **tally([r for r in priced if r["day"] == d])) for d in days]
    window = [r for r in priced if r["day"] in recent]
    return {
        "days": series,
        "today": series[-1],
        "window": window_report(window, recent),
        "priced_calls": len(priced),
        "unpriced": [
            dict(model=m, **v) for m, v in sorted(unpriced.items(), key=lambda i: -i[1]["tokens"])
        ],
    }


def group(rows: list[dict[str, Any]], key: Any) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        buckets.setdefault(key(r), []).append(r)
    found = [{"name": name, **tally(items)} for name, items in buckets.items()]
    return sorted(found, key=lambda i: -i["cost"])


def window_report(rows: list[dict[str, Any]], recent: set[str]) -> dict[str, Any]:
    over = sum(1 for r in rows if r["ctx"] > CONTEXT_LIMIT)
    return {
        "days": sorted(recent),
        **tally(rows),
        "by_model": group(rows, lambda r: r["model"] or "unknown"),
        "by_kind": group(rows, lambda r: "subagent" if r["sub"] else "main"),
        "by_project": group(rows, lambda r: r["cwd"])[:8],
        "over_200k": {"calls": over, "share": over / len(rows) if rows else None},
        "top_sessions": group(rows, lambda r: r["sid"])[:5],
    }
