from typing import Any

from juno.core.dashboard_fmt import (
    Raw,
    badge,
    clean_thread,
    clock,
    command,
    days_between,
    due_label,
    esc,
    gone,
    is_na,
    money,
    percent,
)
from juno.core.dashboard_text import Text

AGE_WARN_DAYS = 14
SOON_DAYS = 3
TODO_LIMIT = 8
LEVELS = {"good", "warn", "bad"}
QUEUES = (
    ("backlog_suggestions", "q_backlog", "q_backlog_name", "juno backlog list"),
    ("thread_candidates", "q_thread", "q_thread_name", "juno thread pending"),
    ("rule_candidates", "q_rule", "q_rule_name", "juno rule pending"),
)
NA_FIX = {"stale": "juno stale"}
LEVEL_ORDER = {"red": 0, "yellow": 1, "na": 2}

Data = dict[str, Any]
Todo = dict[str, Any]


def header(data: Data, t: Text) -> Raw:
    verdict = data["health"].get("verdict") or {}
    level = str(verdict["level"]) if verdict.get("level") in LEVELS else "good"
    detail = t(
        "verdict_detail", verdict.get("red", 0), verdict.get("yellow", 0), verdict.get("na", 0)
    )
    when = t("updated", clock(data["generated_epoch"]), clock(data["next_rebuild_epoch"]))
    return Raw(
        f'<header class="top"><div class="state lv-{level}" title="{esc(detail)}">'
        f'<span class="dot"></span><b>{esc(t("state_title", t("lv_" + level)))}</b></div>'
        f'<div class="meta"><span>{esc(when)}</span>'
        f'<button id="theme" type="button" hidden>{esc(t("theme"))}</button></div></header>'
    )


def tile(num: object, label: str, sub: object = "") -> str:
    return (
        f'<div class="kpi"><div class="num">{esc(num)}</div><div class="lbl">{esc(label)}</div>'
        f'<div class="sub">{esc(sub)}</div></div>'
    )


def kpi_threads(block: Data, t: Text) -> str:
    if is_na(block):
        return tile("—", t("kpi_threads"), gone(block, t))
    old = sum(1 for i in block["items"] if (i.get("age") or 0) >= AGE_WARN_DAYS)
    sub = t("kpi_old", old, AGE_WARN_DAYS) if old else ""
    return tile(len(block["items"]), t("kpi_threads"), sub)


def kpi_backlog(block: Data, t: Text) -> str:
    if is_na(block):
        return tile("—", t("kpi_backlog"), gone(block, t))
    p1 = sum(1 for i in block["items"] if i["priority"] == "p1")
    return tile(block["open"], t("kpi_backlog"), t("kpi_p1", p1) if p1 else "")


def kpi_cost(block: Data, t: Text) -> str:
    if is_na(block):
        return tile("—", t("kpi_cost"), gone(block, t))
    return tile(
        money(block["today"]["cost"], t),
        t("kpi_cost"),
        t("kpi_cost_sub", money(block["window"]["cost"], t)),
    )


def kpi_memory(recall: Data, t: Text) -> str:
    if is_na(recall):
        return tile("—", t("kpi_memory"), gone(recall, t))
    if not recall.get("with_evidence"):
        return tile("—", t("kpi_memory"), t("kpi_nodata"))
    return tile(percent(recall["pack_recall"], t), t("kpi_memory"), t("kpi_memory_sub"))


def kpis(data: Data, t: Text) -> Raw:
    today, exp = data["today"], data["experiments"]
    cells = (
        kpi_threads(today["threads"], t),
        kpi_backlog(today["backlog"], t),
        kpi_cost(data["cost"]["usage"], t),
        kpi_memory(exp["recall"], t),
    )
    return Raw(f'<div class="kpis">{"".join(cells)}</div>')


def todo(
    text: str, badges: list[Raw] | tuple[Raw, ...] = (), cmd: str = "", title: str = ""
) -> Todo:
    return {"text": text, "badges": list(badges), "cmd": cmd, "title": title or text}


def todo_gone(text: str, block: Data) -> Todo:
    return todo(text, title=block["unavailable"])


def backlog_todos(block: Data, today: str, t: Text) -> list[Todo]:
    if is_na(block):
        return [todo_gone(t("backlog_gone"), block)]
    rows = []
    for item in block["items"]:
        if not (item["urgent"] or item["status"] == "doing"):
            continue
        marks = [badge(item["priority"])]
        if item["status"] == "doing":
            marks.append(badge(t("doing"), "accent"))
        if item.get("due") and days_between(item["due"], today) <= SOON_DAYS:
            marks.append(due_label(item["due"], today, t))
        rows.append(todo(item["title"], marks, "juno backlog show " + item["slug"]))
    return rows


def queue_todos(queues: Data, t: Text) -> list[Todo]:
    if is_na(queues):
        return []
    rows = []
    for key, label, name, cmd in QUEUES:
        value = queues.get(key)
        if isinstance(value, dict) and is_na(value):
            rows.append(todo_gone(t("queue_gone", t(name)), value))
        elif value:
            rows.append(todo(f"{value} {t(label)}", cmd=cmd))
    return rows


def health_text(row: Data, t: Text) -> str:
    if row["level"] == "na":
        if row["id"] == "stale" and "not found" in row["detail"]:
            return t("stale_never")
        return t("row_unreadable", row["label"])
    return f"{row['label']}: {row['detail']}"


def health_todos(data: Data, t: Text) -> list[Todo]:
    rows = [
        r
        for r in data["health"].get("rows") or []
        if r["level"] != "green" and (r["id"] != "queues" or r["level"] == "na")
    ]
    out = []
    for r in sorted(rows, key=lambda r: LEVEL_ORDER.get(r["level"], 3)):
        cmd = r.get("fix") or NA_FIX.get(r["id"], "")
        if r["level"] == "red":
            mark = badge(t("lv_bad"), "bad")
        elif r["level"] == "yellow":
            mark = badge(t("lv_warn"), "warn")
        else:
            mark = badge(t("unreadable"))
        out.append(todo(health_text(r, t), [mark], cmd, r["detail"]))
    return out


def thread_todos(block: Data, t: Text) -> list[Todo]:
    if is_na(block):
        return [todo_gone(t("threads_gone"), block)]
    rows = []
    for item in sorted(block["items"], key=lambda i: -(i.get("age") or 0)):
        age, text = item.get("age") or 0, clean_thread(item["text"])
        if item.get("due_state") == "past":
            rows.append(
                todo(t("thread_overdue", item["group"]), [badge(t("overdue"), "bad")], title=text)
            )
        elif age >= AGE_WARN_DAYS:
            rows.append(
                todo(
                    t("thread_aging", item["group"], age),
                    [badge(t("days", age), "warn")],
                    title=text,
                )
            )
    return rows


def todo_rows(data: Data, t: Text) -> list[Todo]:
    queues = data["health"].get("queues", {"unavailable": "queues missing"})
    return (
        backlog_todos(data["today"]["backlog"], data["today_date"], t)
        + queue_todos(queues, t)
        + health_todos(data, t)
        + thread_todos(data["today"]["threads"], t)
    )


def todo_item(row: Todo) -> str:
    cmd = str(command(row["cmd"])) if row["cmd"] else ""
    return (
        f'<li><span class="tx" title="{esc(row["title"])}">{esc(row["text"])}</span>'
        f'<span class="bds">{"".join(str(b) for b in row["badges"])}</span>{cmd}</li>'
    )


def todo_list(data: Data, t: Text) -> Raw:
    rows = todo_rows(data, t)
    title = esc(t("todo"))
    if not rows:
        return Raw(
            f'<section class="todo"><h2>{title}</h2>'
            f'<p class="empty">{esc(t("todo_empty"))}</p></section>'
        )
    more = (
        f'<li class="more">{esc(t("todo_more", len(rows) - TODO_LIMIT))}</li>'
        if len(rows) > TODO_LIMIT
        else ""
    )
    items = "".join(todo_item(r) for r in rows[:TODO_LIMIT])
    return Raw(
        f'<section class="todo"><h2>{title}<span class="count">{len(rows)}</span></h2>'
        f"<ul>{items}{more}</ul></section>"
    )
