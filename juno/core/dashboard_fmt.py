import datetime
import html
import time
from typing import Any

from juno.core.dashboard_text import Text

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


def day_of(epoch: float) -> str:
    return datetime.date.fromtimestamp(epoch).isoformat()


def command(text: str) -> Raw:
    return Raw(f'<code class="cmd">{esc(text)}</code>')


def commands(text: str) -> Raw:
    return Raw(" ".join(str(command(c)) for c in str(text).split(" ; ") if c))


def note(block: Block, t: Text) -> Raw:
    raw = str(block["unavailable"])
    return Raw(f'<span class="na" title="{esc(raw)}">{esc(t("unreadable"))}</span>')
