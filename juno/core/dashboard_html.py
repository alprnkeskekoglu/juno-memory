from typing import Any

from juno.core.dashboard_assets import CSS, JS, THEME_JS
from juno.core.dashboard_fmt import (
    SINCE_RE,
    Raw,
    badge,
    clean_thread,
    commands,
    days_between,
    due_label,
    esc,
    fold,
    gone,
    is_na,
    meter,
    short_date,
)
from juno.core.dashboard_panels import section_cost, section_memory
from juno.core.dashboard_text import Text
from juno.core.dashboard_top import AGE_WARN_DAYS, NA_FIX, header, kpis, todo_list

CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:"
CARD_ROWS = 8

Data = dict[str, Any]


def filter_key(*parts: object) -> str:
    return " ".join(str(p) for p in parts if p).casefold()


def thread_row(item: Data, today: str, t: Text) -> str:
    due = item.get("due")
    if due and (item.get("due_state") in ("past", "due") or days_between(due, today) == 1):
        when: object = due_label(due, today, t)
    else:
        when = Raw(f'<span class="muted">{esc(short_date(due, t))}</span>') if due else ""
    text = clean_thread(item["text"])
    late = ' class="late-row"' if item.get("due_state") == "past" else ""
    key = esc(filter_key(*item["tags"], SINCE_RE.sub("", item["text"])))
    return (
        f'<li{late} data-f="{key}"><span class="tx" title="{esc(text)}">{esc(text)}</span>'
        f"{esc(when)}</li>"
    )


def thread_group(name: str, items: list[Data], today: str, t: Text) -> str:
    ages = [i["age"] for i in items if i.get("age") is not None]
    if ages:
        age = badge(t("oldest", max(ages)), "warn" if max(ages) >= AGE_WARN_DAYS else "")
    else:
        age = badge(t("undated"))
    rows = "".join(thread_row(i, today, t) for i in items)
    return (
        f'<div class="grp"><div class="ghd"><b>{esc(name)}</b>'
        f'<span class="muted">{esc(t("threads_count", len(items)))}</span>{esc(age)}</div>'
        f"<ul>{rows}</ul></div>"
    )


def group_order(entry: tuple[str, list[Data]]) -> tuple[bool, int, str]:
    name, items = entry
    overdue = any(i.get("due_state") == "past" for i in items)
    ages = [i["age"] for i in items if i.get("age") is not None]
    return (not overdue, -(max(ages) if ages else -1), name)


def section_threads(data: Data, t: Text) -> Raw:
    block, today = data["today"]["threads"], data["today_date"]
    bar = (
        '<div id="filterbar" hidden><input id="q" type="search"'
        f' placeholder="{esc(t("filter_ph"))}" aria-label="{esc(t("filter_label"))}"></div>'
    )
    if is_na(block):
        return fold(t("sec_threads"), t("unreadable"), gone(block, t))
    groups: dict[str, list[Data]] = {}
    for item in block["items"]:
        groups.setdefault(item["group"], []).append(item)
    body = (
        "".join(thread_group(n, i, today, t) for n, i in sorted(groups.items(), key=group_order))
        or f'<p class="muted">{esc(t("no_threads"))}</p>'
    )
    return fold(t("sec_threads"), t("sec_threads_sum", len(block["items"])), bar + body)


def card_row(card: Data, t: Text) -> str:
    day, _, rest = card["time"].partition(" ")
    try:
        when = f"{short_date(day, t)} {rest[:5]}"
    except ValueError, IndexError:
        when = card["time"]
    key = badge(card["key"]) if card["key"] and card["key"] != "-" else ""
    return (
        f'<li><span class="muted when">{esc(when)}</span>{esc(key)}'
        f'<span class="tx" title="{esc(card["label"])}">{esc(card["label"])}</span></li>'
    )


def section_cards(data: Data, t: Text) -> Raw:
    block = data["today"]["handoffs"]
    if is_na(block):
        return fold(t("sec_cards"), t("unreadable"), gone(block, t))
    cards = block["cards"][:CARD_ROWS]
    body = (
        f"<ul>{''.join(card_row(c, t) for c in cards)}</ul>"
        if cards
        else f'<p class="muted">{esc(t("no_cards"))}</p>'
    )
    return fold(t("sec_cards"), t("sec_cards_sum", block["total"]), body)


def health_row(r: Data, t: Text) -> str:
    na = r["level"] == "na"
    value = gone({"unavailable": r["detail"]}, t) if na else esc(r["detail"])
    fix = commands(r.get("fix") or NA_FIX.get(r["id"], "")) if r.get("fix") or na else ""
    return (
        f'<tr><td class="nm"><span class="lv lv-{esc(r["level"])}"><span class="dot"></span></span>'
        f'{esc(r["label"])}</td><td>{value}</td><td class="fx">{esc(fix)}</td></tr>'
    )


def index_bar(coverage: Data, t: Text) -> str:
    idx = coverage.get("index") if isinstance(coverage, dict) else None
    label = esc(t("g_index"))
    if idx is None or is_na(idx):
        block = idx or coverage or {"unavailable": "missing"}
        return f'<p class="cov">{label}: {gone(block, t)}</p>'
    share = 1.0 if not idx["vault"] else idx["indexed"] / idx["vault"]
    return (
        f'<p class="cov"><span>{label}</span>'
        f"<span>{esc(t('g_index_detail', idx['indexed'], idx['vault']))}</span></p>{meter(share)}"
    )


def health_summary(verdict: Data | None, t: Text) -> str:
    red, yellow, na = (
        (verdict or {}).get("red", 0),
        (verdict or {}).get("yellow", 0),
        (verdict or {}).get("na", 0),
    )
    parts = [
        t(k, n) for k, n in (("health_bad", red), ("health_warn", yellow), ("health_na", na)) if n
    ]
    return " · ".join(parts) or t("health_ok")


def section_health(data: Data, t: Text) -> Raw:
    h = data["health"]
    rows = "".join(health_row(r, t) for r in h.get("rows") or [])
    table = f"<table>{rows}</table>" if rows else f'<p class="muted">{esc(t("no_data"))}</p>'
    body = table + index_bar(h.get("coverage", {"unavailable": "coverage missing"}), t)
    return fold(t("sec_health"), health_summary(h.get("verdict"), t), body)


def render(data: Data, t: Text) -> str:
    top = (header(data, t), kpis(data, t), todo_list(data, t))
    sections = (
        section_threads(data, t),
        section_cards(data, t),
        section_health(data, t),
        section_cost(data, t),
        section_memory(data, t),
    )
    return (
        f'<!DOCTYPE html>\n<html lang="{t.lang}"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Juno</title>'
        f"<script>{THEME_JS}</script><style>{CSS}</style></head><body><main>"
        f'{"".join(str(s) for s in top)}<div class="acc">{"".join(str(s) for s in sections)}</div>'
        f"</main><script>{JS}</script></body></html>\n"
    )
