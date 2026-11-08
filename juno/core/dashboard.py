import re
from collections.abc import Sequence
from typing import Any

from juno.core import dashboard_collect as collector
from juno.core import dashboard_html as page

Sources = collector.Sources


def ticket_pattern(prefixes: Sequence[str]) -> re.Pattern[str] | None:
    names = [re.escape(p) for p in prefixes if p]
    if not names:
        return None
    return re.compile(r"(?:" + "|".join(names) + r")-[0-9]+", re.IGNORECASE)


def generate(src: Sources, now: float) -> tuple[dict[str, Any], str]:
    data = collector.collect(src, now)
    return data, page.render(data, src.t)
