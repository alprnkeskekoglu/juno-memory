# ruff: noqa: E501
import datetime
from typing import Any

from juno.core import dashboard_charts as charts
from juno.core.dashboard_charts import Series
from juno.core.dashboard_fmt import (
    Raw,
    command,
    commands,
    day_of,
    esc,
    is_na,
    money,
    percent,
    short_date,
    stamp,
)
from juno.core.dashboard_text import Text

Data = dict[str, Any]

ICONS = {
    "ok": '<path d="M3.5 8.5l3 3 6-7"/>',
    "warn": '<path d="M8 2.5l6 10.5H2z"/><path d="M8 7v3M8 11.6v.1"/>',
    "bad": '<path d="M4 4l8 8M12 4l-8 8"/>',
    "wait": '<path d="M3.5 8h9"/>',
    "run": '<circle cx="8" cy="8" r="3" fill="currentColor"/>',
    "stop": '<rect x="4.5" y="4.5" width="7" height="7" fill="currentColor"/>',
    "none": '<circle cx="8" cy="8" r="3.5"/>',
}
STATE_ICON = {"running": "run", "stopped": "stop", "decided": "ok", "not_started": "none"}
LEVEL_ICON = {"green": "ok", "yellow": "warn", "red": "bad", "na": "wait"}
VERDICT = {"good": ("ok", "st_good"), "warn": ("warn", "st_warn"), "bad": ("bad", "st_bad")}
KIND_COLORS = {
    "card": "var(--s1)",
    "knowledge": "var(--s2)",
    "decision": "var(--s3)",
    "pitfall": "var(--s4)",
    "rule": "var(--s5)",
    "fix": "var(--s6)",
    "other": "var(--other)",
}
DAY = 86400
QUEUES = (
    ("backlog_suggestions", "q_backlog_n"),
    ("thread_candidates", "q_thread_n"),
    ("rule_candidates", "q_rule_n"),
)
HEALTH_TERMS = {
    "runs": "h_runs",
    "hook_errors": "h_hooks",
    "queues": "h_queues",
    "stale": "h_stale",
    "index": "h_index",
}
GLOSSARY = (
    "recall pack search atleast evidence writes own_spend cc_usage price_est experiments"
    " arm_linked arm_self arm_next segment min_each ready deadline k_card k_knowledge k_decision"
    " k_pitfall k_rule k_fix k_other h_runs h_hooks h_queues h_stale h_index"
).split()
NA_FIX = {"stale": "juno stale"}
QUOTA_LENGTH = {"five_hour": 5 * 3600, "seven_day": 7 * DAY}
PERCENT_KEYS = ("percent", "used_percentage", "utilization", "used")
RESET_KEYS = ("resets_at", "reset_at", "resets")


def icon(name: str) -> str:
    return (
        f'<svg class="ic" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor"'
        f' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>'
    )


def hint(tid: str, t: Text) -> Raw:
    name, text = t("tn_" + tid), t("x_" + tid)
    return Raw(
        f'<button type="button" class="info" data-id="{tid}" aria-label="{esc(name + ": " + text)}"'
        f' data-tip="{charts.tip(name, [(text, "", "transparent")])}">i</button>'
    )


def pill(kind: str, name: str, text: str, title: str = "") -> str:
    tip = f' title="{esc(title)}"' if title else ""
    return f'<span class="pill {kind}"{tip}>{icon(name)}<span>{esc(text)}</span></span>'


def header(data: Data, t: Text) -> str:
    verdict = data["health"].get("verdict") or {}
    level = str(verdict["level"]) if verdict.get("level") in VERDICT else "good"
    name, label = VERDICT[level]
    detail = t(
        "verdict_detail", verdict.get("red", 0), verdict.get("yellow", 0), verdict.get("na", 0)
    )
    return (
        f'<header class="top"><div><h1>{esc(t("title"))}</h1>'
        f'<p class="muted">{esc(t("updated", stamp(data["generated_epoch"], t)))}</p></div>'
        f"{pill('st-' + level, name, t(label), detail)}</header>"
    )


def tile(
    label: str,
    figure: str,
    subs: list[str],
    hero: bool = False,
    title: str = "",
    hints: tuple[Raw, ...] = (),
) -> str:
    cls = "tile hero" if hero else "tile"
    tip = f' title="{esc(title)}"' if title else ""
    lines = "".join(f"<p>{esc(x)}</p>" for x in subs)
    return (
        f'<article class="{cls}"><h2>{esc(label)}{"".join(hints)}</h2>'
        f'<div class="fig"{tip}>{esc(figure)}</div><div class="subs">{lines}</div></article>'
    )


def recall_tile(recall: Data, t: Text) -> str:
    label, h = t("tile_recall"), (hint("recall", t),)
    if is_na(recall):
        return tile(label, "—", [t("unreadable")], True, recall["unavailable"], h)
    n = recall.get("with_evidence")
    if not n:
        return tile(label, "—", [t("tile_nodata")], True, "", h)
    pack = Raw(
        f"{esc(t('recall_pack_lbl'))}{hint('pack', t)} · "
        f"{esc(t('at_least', percent(recall['pack_wilson_lower'], t)))}{hint('atleast', t)} · "
        f"{esc(t('of_n', recall['pack_hits'], n))}{hint('evidence', t)}"
    )
    plain = Raw(
        f"{esc(t('recall_search_lbl'))}{hint('search', t)} · {esc(percent(recall['recall'], t))}"
        f" · {esc(t('at_least', percent(recall['wilson_lower'], t)))}"
    )
    return tile(label, percent(recall["pack_recall"], t), [pack, plain], True, "", h)


def writes_tile(writes: Data, t: Text) -> str:
    label, h = t("tile_writes"), (hint("writes", t),)
    if is_na(writes):
        return tile(label, "—", [t("unreadable")], title=writes["unavailable"], hints=h)
    return tile(label, str(writes["week"]), [t("writes_today", writes["today"])], hints=h)


def spend_tile(juno: Data, today: str, t: Text) -> str:
    label, h = t("tile_spend"), (hint("own_spend", t),)
    cards = juno.get("cards") if isinstance(juno, dict) else None
    if is_na(juno) or not isinstance(cards, dict):
        reason = juno.get("unavailable", "") if isinstance(juno, dict) else ""
        return tile(label, "—", [t("unreadable")], title=reason, hints=h)
    spend = cards["spend"]
    week = sum(spend.get(d, 0.0) for d in juno["days"][-7:])
    subs = [t("spend_week", money(week, t))]
    if cards.get("budget"):
        subs.append(t("spend_budget", money(cards["budget"], t)))
    return tile(label, money(spend.get(today, 0.0), t), subs, hints=h)


def tiles(data: Data, t: Text) -> str:
    cells = (
        recall_tile(data["experiments"]["recall"], t),
        writes_tile(data["writes"], t),
        spend_tile(data["cost"]["juno"], data["today_date"], t),
    )
    return f'<section class="tiles">{"".join(cells)}</section>'


def card(title: str, sub: str, body: str, hints: tuple[Raw, ...] = ()) -> str:
    return (
        f'<section class="card"><div class="ch"><h2>{esc(title)}{"".join(hints)}</h2>'
        f'<p class="muted">{esc(sub)}</p></div>{body}</section>'
    )


def empty(text: str, title: str = "") -> str:
    tip = f' title="{esc(title)}"' if title else ""
    return f'<p class="empty"{tip}>{esc(text)}</p>'


def week_label(start: str, t: Text) -> str:
    return short_date(start, t)


def section_recall(data: Data, t: Text) -> str:
    block = data["experiments"]["recall_weekly"]
    title, sub = t("sec_recall"), t("recall_sub")
    if is_na(block):
        return card(title, sub, empty(t("unreadable"), block["unavailable"]))
    shown = [w for w in block["weeks"] if w["n"]]
    if not shown:
        return card(title, sub, empty(t("tile_nodata")))
    weeks = block["weeks"][block["weeks"].index(shown[0]) :]
    series = [
        Series("pack", t("recall_pack_lbl"), "var(--s1)", str(hint("pack", t))),
        Series("plain", t("recall_search_lbl"), "var(--s2)", str(hint("search", t))),
    ]
    values: list[dict[str, float | None]] = [
        {"pack": w["pack_recall"] if w["n"] else None, "plain": w["recall"] if w["n"] else None}
        for w in weeks
    ]
    extra = [t("recall_n", w["n"]) for w in weeks]
    chart = charts.lines(
        [week_label(w["start"], t) for w in weeks],
        series,
        values,
        extra,
        lambda v: percent(v, t),
        t("recall_aria"),
        t,
        [t("col_week"), series[0].label, series[1].label, t("col_n")],
        len(shown) >= 2,
    )
    note = "" if len(shown) >= 2 else empty(t("recall_few"))
    return card(title, sub, note + str(chart), (hint("recall", t), hint("evidence", t)))


def section_writes(data: Data, t: Text) -> str:
    block = data["writes"]
    title, sub = t("sec_writes"), t("writes_sub")
    if is_na(block):
        return card(title, sub, empty(t("unreadable"), block["unavailable"]))
    if not block["month"]:
        return card(title, sub, empty(t("writes_none")))
    series = [
        Series(k, t("k_" + k), KIND_COLORS[k], str(hint("k_" + k, t))) for k in block["kinds"]
    ]
    days = block["days"]
    chart = charts.columns(
        [short_date(d, t) for d in days],
        charts.axis_labels(days, lambda d: short_date(d, t)),
        series,
        [{k: float(v) for k, v in block["by_day"][d].items()} for d in days],
        lambda v: f"{v:g}",
        charts.tick_text,
        t("writes_aria"),
        t,
        t("col_day"),
        t("col_total"),
    )
    return card(title, sub, str(chart), (hint("writes", t),))


def spend_chart(days: list[str], spend: dict[str, float], color: str, aria: str, t: Text) -> Raw:
    return charts.columns(
        [short_date(d, t) for d in days],
        charts.axis_labels(days, lambda d: short_date(d, t)),
        [Series("cost", t("col_spend"), color)],
        [{"cost": spend.get(d, 0.0)} for d in days],
        lambda v: money(v, t),
        lambda v: money(v, t),
        aria,
        t,
        t("col_day"),
    )


def quota_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in PERCENT_KEYS:
            found = value.get(key)
            if isinstance(found, (int, float)) and not isinstance(found, bool):
                return float(found)
    return None


def quota_reset(value: object) -> float | None:
    raw = (
        next((value[k] for k in RESET_KEYS if k in value), None)
        if isinstance(value, dict)
        else None
    )
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    try:
        return datetime.datetime.fromisoformat(raw).timestamp() if isinstance(raw, str) else None
    except ValueError:
        return None


def quota_row(key: str, value: object, now: float, t: Text) -> str:
    used, reset = quota_number(value), quota_reset(value)
    if used is None:
        return ""
    used = used / 100 if used > 1 else used
    text = t("used", percent(used, t))
    if reset and reset > now:
        passed = max(0.0, min(1.0, 1 - (reset - now) / QUOTA_LENGTH[key]))
        text += " · " + t("elapsed", percent(passed, t), stamp(reset, t))
    return f'<p class="qrow">{esc(t("w_" + key))}: {esc(text)}</p>{bar(used)}'


def bar(share: float, label: str = "") -> str:
    width = max(0, min(100, round(share * 100)))
    aria = f' aria-label="{esc(label)}"' if label else ""
    return (
        f'<div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100"'
        f' aria-valuenow="{width}"{aria}><i style="width:{width}%"></i></div>'
    )


def quota_html(block: Data, now: float, t: Text) -> str:
    rows = ""
    if not is_na(block):
        rows = "".join(
            quota_row(k, block["windows"][k], now, t) for k in QUOTA_LENGTH if k in block["windows"]
        )
    return rows or f'<p class="muted note">{esc(t("quota_note"))}</p>'


def usage_notes(u: Data, t: Text) -> str:
    out = f'<p class="muted note">{esc(t("price_note"))}{hint("price_est", t)}</p>'
    if u["unpriced"]:
        names = ", ".join(t("unpriced_item", m["model"], m["calls"]) for m in u["unpriced"])
        out += (
            f'<p class="warnline">{icon("warn")}'
            f"<span>{esc(t('unpriced', names))} — {esc(t('unpriced_tail'))}</span></p>"
        )
    return out


def section_cost(data: Data, t: Text) -> str:
    juno, usage, quota = data["cost"]["juno"], data["cost"]["usage"], data["cost"]["quota"]
    parts = []
    cards = juno.get("cards") if isinstance(juno, dict) else None
    if is_na(juno) or not isinstance(cards, dict):
        reason = juno.get("unavailable", "") if isinstance(juno, dict) else ""
        parts.append(
            f"<h3>{esc(t('cost_juno'))}{hint('own_spend', t)}</h3>{empty(t('unreadable'), reason)}"
        )
    else:
        sub = t("cost_budget", money(cards["budget"], t)) if cards.get("budget") else ""
        body = (
            str(spend_chart(juno["days"], cards["spend"], "var(--s1)", t("cost_juno_aria"), t))
            if any(cards["spend"].values())
            else empty(t("cost_none"))
        )
        parts.append(
            f'<h3>{esc(t("cost_juno"))}{hint("own_spend", t)}</h3><p class="muted note">{esc(sub)}</p>{body}'
        )
    if is_na(usage):
        parts.append(
            f"<h3>{esc(t('cost_cc'))}{hint('cc_usage', t)}</h3>{empty(t('unreadable'), usage['unavailable'])}"
        )
    else:
        spend = {d["day"]: d["cost"] for d in usage["days"]}
        body = (
            str(
                spend_chart(
                    [d["day"] for d in usage["days"]], spend, "var(--s2)", t("cost_cc_aria"), t
                )
            )
            if any(spend.values())
            else empty(t("cost_none"))
        )
        parts.append(
            f"<h3>{esc(t('cost_cc'))}{hint('cc_usage', t)}</h3>{body}{usage_notes(usage, t)}"
        )
    parts.append(quota_html(quota, data["generated_epoch"], t))
    return card(t("sec_cost"), "", "".join(parts))


def arm_view(row: Data, now: float, t: Text) -> str:
    name = row["name"]
    status = row["status"] if row["status"] in STATE_ICON else "not_started"
    label = t("arm_" + name) if f"arm_{name}" in t.table else name
    counts = row.get("n") if isinstance(row.get("n"), dict) else None
    min_n = row.get("min_n") or 0
    tip = hint("arm_" + name, t) if f"x_arm_{name}" in t.table else ""
    head = (
        f'<div class="arm-h"><b>{esc(label)}</b>{tip}{command(name)}'
        f"{pill('neutral', STATE_ICON[status], t('st_' + status))}</div>"
    )
    if status == "not_started" or not row.get("started"):
        plan = esc(t("exp_plan", min_n, row.get("max_days") or 0))
        return f'<li class="arm">{head}<div class="arm-m"><span>{plan}</span></div>{bar(0)}</li>'
    on, off = (counts or {}).get("on", 0), (counts or {}).get("off", 0)
    share = min(on, off) / min_n if min_n else 0.0
    started = day_of(row["started"])
    deadline = day_of(row["started"] + row["max_days"] * DAY)
    meta = [t("exp_started", short_date(started, t)), t("exp_sides", on, off, min_n)]
    if row.get("stop_fired"):
        ready = pill("bad", "bad", t("exp_stop"))
    elif row.get("ready") and min(on, off) >= min_n:
        ready = pill("good", "ok", t("exp_ready"))
    else:
        ready = pill("neutral", "wait", t("exp_wait"), row.get("reason") or "")
    by = [t("exp_by", short_date(deadline, t), row["max_days"])]
    days_in = (now - row["started"]) / DAY
    low = min(on, off)
    if status == "running" and days_in >= 1 and 0 < low < min_n:
        eta = now + (min_n - low) / (low / days_in) * DAY
        by.append(t("exp_pace", short_date(day_of(eta), t)))
    spans = (
        f"<span>{esc(meta[0])}</span>"
        f"<span>{esc(meta[1])}{hint('segment', t)}{hint('min_each', t)}</span>"
    )
    return (
        f'<li class="arm">{head}<div class="arm-m">{spans}</div>{bar(share, t("exp_progress"))}'
        f'<div class="arm-f">{ready}{hint("ready", t)}'
        f'<span class="muted">{esc(" · ".join(by))}{hint("deadline", t)}</span></div></li>'
    )


def section_experiments(data: Data, t: Text) -> str:
    block = data["arms"]
    title, sub = t("sec_exp"), t("exp_sub")
    if is_na(block):
        return card(title, sub, empty(t("unreadable"), block["unavailable"]))
    if not block["items"]:
        return card(title, sub, empty(t("exp_none")))
    rows = "".join(arm_view(r, data["generated_epoch"], t) for r in block["items"])
    return card(title, sub, f'<ul class="arms">{rows}</ul>', (hint("experiments", t),))


def health_summary(verdict: Data | None, t: Text) -> str:
    v = verdict or {}
    parts = [
        t(k, v[f])
        for k, f in (("health_bad", "red"), ("health_warn", "yellow"), ("health_na", "na"))
        if v.get(f)
    ]
    return " · ".join(parts) or t("health_ok")


def health_row(r: Data, t: Text) -> str:
    level = r["level"]
    name = LEVEL_ICON.get(level, "wait")
    kind = {"green": "good", "yellow": "warn", "red": "bad"}.get(level, "neutral")
    tip = hint(HEALTH_TERMS[r["id"]], t) if r["id"] in HEALTH_TERMS else ""
    if level == "green":
        return (
            f'<li class="hrow ok">{icon(name)}<b>{esc(r["label"])}</b>{tip}'
            f'<span class="muted">{esc(r["detail"])}</span></li>'
        )
    fix = r.get("fix") or NA_FIX.get(r["id"], "")
    detail, raw = r["detail"], ""
    if level == "na":
        raw = detail
        detail = (
            t("stale_never")
            if r["id"] == "stale" and "not found" in detail
            else t("row_unreadable", r["label"])
        )
    fix_html = (
        f'<p class="fix"><span class="muted">{esc(t("fix"))}</span>{commands(fix)}</p>'
        if fix
        else ""
    )
    return (
        f'<li class="hrow open {kind}"><div class="hh">{icon(name)}<b>{esc(r["label"])}</b>{tip}</div>'
        f'<p title="{esc(raw)}">{esc(detail)}</p>{fix_html}</li>'
    )


def health_lines(health: Data, t: Text) -> str:
    out = []
    hooks = health.get("hook_errors")
    if isinstance(hooks, dict) and not is_na(hooks):
        out.append(t("hooks_line", hooks["last_24h"], hooks["count"]))
    queues = health.get("queues")
    if isinstance(queues, dict) and not is_na(queues):
        parts = [
            f"{queues[k]} {t(label)}"
            for k, label in QUEUES
            if isinstance(queues.get(k), int) and queues[k]
        ]
        out.append(t("queues_line", ", ".join(parts)) if parts else t("queues_empty"))
    return "".join(f'<p class="muted hline">{esc(x)}</p>' for x in out)


def section_health(data: Data, t: Text) -> str:
    health = data["health"]
    rows = sorted(health.get("rows") or [], key=lambda r: r["level"] == "green")
    body = f'<ul class="hrows">{"".join(health_row(r, t) for r in rows)}</ul>'
    if not rows:
        body = empty(t("no_data"))
    return card(
        t("sec_health"), health_summary(health.get("verdict"), t), body + health_lines(health, t)
    )


def section_glossary(t: Text) -> str:
    items = "".join(f"<dt>{esc(t('tn_' + k))}</dt><dd>{esc(t('x_' + k))}</dd>" for k in GLOSSARY)
    return (
        f'<details class="card gloss"><summary><h2>{esc(t("sec_gloss"))}</h2>'
        f'<span class="muted">{esc(t("gloss_sub"))}</span></summary><dl>{items}</dl></details>'
    )
