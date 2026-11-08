import datetime
import html
import re
import time
from typing import Any

from juno.core.dashboard_text import Text

SINCE_RE = re.compile(r"\s*·?\s*since \d{4}-\d{2}-\d{2}")
MODEL_DATE_RE = re.compile(r"-\d{8}$")
MISSING_WORDS = ("not found", "FileNotFoundError", " missing")

Block = dict[str, Any]


class Raw(str):
    pass


def esc(value: object) -> str:
    return value if isinstance(value, Raw) else html.escape(str(value), quote=True)


def is_na(block: object) -> bool:
    return isinstance(block, dict) and "unavailable" in block


def money(value: float, t: Text) -> str:
    dec = t.table["dec"]
    if value >= 10:
        return "$" + f"{round(value):,}".replace(",", t.table["thou"])
    if value == 0 or value >= 0.005:
        return f"${value:.2f}".replace(".", dec)
    return f"${value:.3f}".replace(".", dec)


def percent(share: float | None, t: Text, calls: int = 0) -> str:
    if share is None:
        return t("no_data")
    whole = round(share * 100)
    return t("pct", "<1") if whole == 0 and calls else t("pct", str(whole))


def short_date(day: str, t: Text) -> str:
    _, month, number = map(int, day.split("-"))
    return f"{number} {t.months[month - 1]}"


def stamp(epoch: float, t: Text) -> str:
    local = time.localtime(epoch)
    return f"{short_date(time.strftime('%Y-%m-%d', local), t)} {time.strftime('%H:%M', local)}"


def clock(epoch: float) -> str:
    return time.strftime("%H:%M", time.localtime(epoch))


def clean_thread(text: str) -> str:
    return SINCE_RE.sub("", text).strip()


def model_name(raw: object) -> str:
    name = MODEL_DATE_RE.sub("", re.sub(r"\[.*?\]$", "", str(raw)))
    parts = name.replace("claude-", "", 1).split("-")
    digits = [p for p in parts if p.isdigit()]
    words = [p for p in parts if not p.isdigit()]
    if not words:
        return str(raw)
    return " ".join([w.capitalize() for w in words] + ([".".join(digits)] if digits else []))


def command(text: str) -> Raw:
    return Raw(f'<code class="cmd">{esc(text)}</code>')


def commands(text: str) -> Raw:
    return Raw(" ".join(str(command(c)) for c in str(text).split(" ; ") if c))


def gone(block: Block, t: Text, cmd: str = "") -> Raw:
    raw = str(block["unavailable"])
    word = t("no_data") if any(w in raw for w in MISSING_WORDS) else t("unreadable")
    extra = " " + str(commands(cmd)) if cmd else ""
    return Raw(f'<span class="na" title="{esc(raw)}">{word}</span>{extra}')


def days_between(later: str, earlier: str) -> int:
    return (datetime.date.fromisoformat(later) - datetime.date.fromisoformat(earlier)).days


def badge(text: str, kind: str = "") -> Raw:
    return Raw(f'<span class="bd{" " + kind if kind else ""}">{esc(text)}</span>')


def due_label(due: str, today: str, t: Text) -> Raw:
    left = days_between(due, today)
    if left < 0:
        return badge(t("overdue"), "bad")
    if left == 0:
        return badge(t("today"), "warn")
    return badge(t("tomorrow") if left == 1 else t("days_left", left), "warn")


def meter(share: float, kind: str = "") -> Raw:
    width = max(0, min(100, round(share * 100)))
    return Raw(
        f'<div class="meter{" " + kind if kind else ""}"><i style="width:{width}%"></i></div>'
    )


def fold(title: str, summary: str, body: object, cls: str = "") -> Raw:
    return Raw(
        f'<details class="sec{" " + cls if cls else ""}"><summary><span class="st">{esc(title)}'
        f'</span><span class="ss">{esc(summary)}</span></summary><div class="sb">{body}</div>'
        "</details>"
    )
