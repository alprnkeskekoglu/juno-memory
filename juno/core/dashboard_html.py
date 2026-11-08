from typing import Any

from juno.core import dashboard_panels as panels
from juno.core.dashboard_assets import CSS, JS, THEME_JS
from juno.core.dashboard_text import Text

CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:"

Data = dict[str, Any]


def render(data: Data, t: Text) -> str:
    body = (
        panels.header(data, t),
        panels.tiles(data, t),
        panels.section_recall(data, t),
        panels.section_experiments(data, t),
        panels.section_writes(data, t),
        panels.section_cost(data, t),
        panels.section_health(data, t),
        panels.section_glossary(t),
    )
    return (
        f'<!DOCTYPE html>\n<html lang="{t.lang}"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="color-scheme" content="light dark"><title>{t("title")}</title>'
        f"<script>{THEME_JS}</script><style>{CSS}</style></head><body><main>"
        f"{''.join(body)}</main><script>{JS}</script></body></html>\n"
    )
