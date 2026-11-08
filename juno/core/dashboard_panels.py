import datetime
from typing import Any

from juno.core.dashboard_fmt import (
    Raw,
    badge,
    command,
    esc,
    fold,
    gone,
    is_na,
    meter,
    model_name,
    money,
    percent,
    short_date,
    stamp,
)
from juno.core.dashboard_text import Text

CHART_DAYS = 7
QUOTA_LENGTH = {"five_hour": 5 * 3600, "seven_day": 7 * 86400}
PERCENT_KEYS = ("percent", "used_percentage", "utilization", "used")
RESET_KEYS = ("resets_at", "reset_at", "resets")

Data = dict[str, Any]


def columns(items: list[tuple[str, float]], label: str, t: Text) -> Raw:
    width, height = 700, 150
    left, top, bottom = 8, 16, 20
    base = height - bottom
    peak = max([v for _, v in items] + [0.0001])
    slot = (width - 2 * left) / max(1, len(items))
    bar_w = min(44.0, slot * 0.6)
    tall = max(range(len(items)), key=lambda i: items[i][1]) if items else -1
    out = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(label)}">',
        f'<line class="base" x1="{left}" x2="{width - left}" y1="{base}" y2="{base}"/>',
    ]
    for i, (name, value) in enumerate(items):
        x = left + i * slot + (slot - bar_w) / 2
        h = value / peak * (base - top)
        if value > 0:
            out.append(
                f'<rect class="bar" x="{x:.1f}" y="{base - h:.1f}" width="{bar_w:.1f}"'
                f' height="{h:.1f}" rx="3"/>'
            )
        if i == tall and value > 0:
            out.append(
                f'<text class="val" x="{x + bar_w / 2:.1f}" y="{base - h - 4:.1f}"'
                f' text-anchor="middle">{esc(money(value, t))}</text>'
            )
        mid = f"{x + bar_w / 2:.1f}"
        out.append(f'<text x="{mid}" y="{height - 5}" text-anchor="middle">{esc(name)}</text>')
    out.append("</svg>")
    return Raw("".join(out))


def stat(value: str, label: str, sub: str = "", over: bool = False) -> str:
    return (
        f'<div class="stat{" over" if over else ""}"><b>{esc(value)}</b><span>{esc(label)}</span>'
        f"<em>{esc(sub)}</em></div>"
    )


def usage_notes(u: Data, t: Text) -> str:
    window, out = u["window"], []
    over = window["over_200k"]
    if window["cost"] > 0:
        model = window["by_model"][0] if window["by_model"] else None
        kinds = {k["name"]: k["cost"] for k in window["by_kind"]}
        parts = (
            [f"{model_name(model['name'])} {percent(model['cost'] / window['cost'], t)}"]
            if model
            else []
        )
        parts.append(t("top_sub", percent(kinds.get("subagent", 0.0) / window["cost"], t)))
        out.append(t("top_model", " · ".join(parts)))
    out.append(t("over_200k", percent(over["share"], t, over["calls"])))
    out.append(t("price_note"))
    notes = "".join(f'<p class="sub">{esc(x)}</p>' for x in out)
    if u["unpriced"]:
        names = ", ".join(t("unpriced_item", m["model"], m["calls"]) for m in u["unpriced"])
        notes += f'<p class="warn">{esc(t("unpriced", names))} — {esc(t("unpriced_tail"))}</p>'
    return notes


def own_stat(block: Data, today: str, t: Text) -> str:
    cards = block.get("cards")
    if is_na(cards) or not isinstance(cards, dict):
        return stat("—", t("stat_own"), t("no_data"))
    spent = cards["spend"].get(today, 0.0)
    budget = cards.get("budget") or 0.0
    text = f"{money(spent, t)} / {money(budget, t)}" if budget else money(spent, t)
    sub = f"{t('own_cards')} {money(spent, t)}"
    return stat(text, t("stat_own"), sub, bool(budget and spent > budget))


def usage_body(data: Data, t: Text) -> str:
    u, own = data["cost"]["usage"], data["cost"]["juno"]
    own_html = (
        stat("—", t("stat_own"), t("no_data"))
        if is_na(own)
        else own_stat(own, data["today_date"], t)
    )
    tiles = (
        stat(money(u["today"]["cost"], t), t("stat_today"), t("calls", u["today"]["calls"])),
        stat(money(u["window"]["cost"], t), t("stat_week"), t("calls", u["window"]["calls"])),
        own_html,
    )
    series = [(short_date(d["day"], t), d["cost"]) for d in u["days"][-CHART_DAYS:]]
    return (
        f'<div class="stats">{"".join(tiles)}</div>'
        f"{columns(series, t('chart_label'), t)}{usage_notes(u, t)}"
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
    kind = ""
    if reset and reset > now:
        passed = 1 - (reset - now) / QUOTA_LENGTH[key]
        passed = max(0.0, min(1.0, passed))
        text += " · " + t("elapsed", percent(passed, t), stamp(reset, t))
        kind = "warn" if used > passed else ""
    return f"<p>{esc(t('w_' + key))}: {esc(text)}</p>{meter(used, kind)}"


def quota_body(block: Data, now: float, t: Text) -> str:
    none = f'<p class="muted">{esc(t("no_quota"))}</p>'
    if is_na(block):
        return f'<p class="muted" title="{esc(block["unavailable"])}">{esc(t("no_quota"))}</p>'
    rows = "".join(
        quota_row(k, block["windows"][k], now, t) for k in QUOTA_LENGTH if k in block["windows"]
    )
    return rows or none


def section_cost(data: Data, t: Text) -> Raw:
    u, quota = data["cost"]["usage"], data["cost"]["quota"]
    if is_na(u):
        return fold(t("sec_cost"), t("unreadable"), gone(u, t))
    body = usage_body(data, t) + quota_body(quota, data["generated_epoch"], t)
    return fold(t("sec_cost"), t("sec_cost_sum", money(u["today"]["cost"], t)), body)


def num_cell(value: object) -> str:
    return "—" if value is None else f"{value:.1f}"


def ab_body(block: Data, t: Text) -> str:
    rows = []
    for arm, v in block["arms"].items():
        name = t("arm_" + arm) if arm in ("cards", "holdout") else arm
        rows.append(
            f"<tr><td>{esc(name)}</td><td>{v['segments']}</td>"
            f"<td>{esc(num_cell(v['prompts_per_segment']))}</td>"
            f"<td>{esc(num_cell(v['cues_per_100_prompts']))}</td></tr>"
        )
    head = (
        f"<tr><th></th><th>{esc(t('ab_sessions'))}</th><th>{esc(t('ab_prompts'))}</th>"
        f"<th>{esc(t('ab_cues'))}</th></tr>"
    )
    return f'<h3>{esc(t("ab_title"))}</h3><table class="ab">{head}{"".join(rows)}</table>'


def gate_body(block: Data, t: Text) -> str:
    last = block["last"]
    head = f"<h3>{esc(t('gate_title'))}</h3>"
    if is_na(last):
        missing = any(w in last["unavailable"] for w in ("not found", "FileNotFoundError"))
        word = (
            f'<span class="na" title="{esc(last["unavailable"])}">'
            f"{esc(t('gate_never') if missing else t('unreadable'))}</span>"
        )
        return f"{head}<p>{word} {command('juno eval gate')}</p>"
    verdict = badge(t("gate_pass"), "ok") if last.get("passed") else badge(t("gate_fail"), "bad")
    why = " · ".join(str(r) for r in last.get("reasons") or [])
    return f"{head}<p>{verdict} {esc(last.get('set') or '')}{esc(' · ' + why if why else '')}</p>"


def recall_body(block: Data, t: Text) -> str:
    head = f"<h3>{esc(t('recall_title'))}</h3>"
    n = block["with_evidence"]
    if not n:
        return head + f'<p class="muted">{esc(t("recall_none"))}</p>'
    line = t(
        "recall_line",
        percent(block["recall"], t),
        block["hits"],
        n,
        percent(block["pack_recall"], t),
        block["pack_hits"],
        n,
    )
    lines = [f"<p>{esc(line)}</p>"]
    missed = [
        t(k, v)
        for k, v in (("recall_silent", block["silent"]), ("recall_wrong", block["wrong"]))
        if v
    ]
    if missed:
        lines.append(f'<p class="sub">{esc(", ".join(missed))}</p>')
    return head + "".join(lines)


def first_sentence(text: object) -> str:
    return " ".join(str(text).split()).split(". ", 1)[0].rstrip(".")


def work_lines(experiments: Data, t: Text) -> str:
    out = []
    ideas, tasks = experiments["ideas"], experiments["tasks"]
    if is_na(ideas):
        out.append(f"<h3>{esc(t('ideas_title'))}</h3><p>{gone(ideas, t)}</p>")
    elif ideas["items"]:
        rows = "".join(
            f'<li><span class="tx" title="{esc(i.get("title") or i.get("id") or "")}">'
            f"{esc(first_sentence(i.get('title') or i.get('id') or ''))}</span></li>"
            for i in ideas["items"]
        )
        out.append(f"<h3>{esc(t('ideas_title'))}</h3><ul>{rows}</ul>")
    count = t("no_data") if is_na(tasks) else len(tasks["items"])
    out.append(f'<p class="sub">{esc(t("tasks_open", count))}</p>')
    return "".join(out)


def section_memory(data: Data, t: Text) -> Raw:
    e = data["experiments"]
    parts = []
    for key, label, build in (
        ("recall", "memory_title", recall_body),
        ("card_ab", "ab_title", ab_body),
        ("gate", "gate_title", gate_body),
    ):
        if is_na(e[key]):
            parts.append(f"<h3>{esc(t(label))}</h3><p>{gone(e[key], t)}</p>")
        else:
            parts.append(build(e[key], t))
    recall = e["recall"]
    ok = not is_na(recall) and recall.get("with_evidence")
    summary = t("recall_sum", percent(recall["pack_recall"], t)) if ok else t("no_data")
    return fold(t("memory_title"), summary, "".join(parts) + work_lines(e, t))
