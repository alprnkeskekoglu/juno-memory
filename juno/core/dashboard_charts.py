# ruff: noqa: E501
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from juno.core.dashboard_fmt import Raw, esc
from juno.core.dashboard_text import Text

WIDTH, HEIGHT = 720, 232
LEFT, RIGHT, TOP, BOTTOM = 46, 12, 22, 28
BAR_MAX = 24.0
BAR_GAP = 2.0
CORNER = 4.0
LABEL_EVERY = 5
STEPS = (1, 2, 2.5, 5, 10)


@dataclass
class Series:
    key: str
    label: str
    color: str
    hint: str = ""


Fmt = Callable[[float], str]


def nice_axis(peak: float, intervals: int = 4) -> tuple[float, list[float]]:
    if peak <= 0:
        return 1.0, [0.0, 1.0]
    raw = peak / intervals
    scale = 10 ** math.floor(math.log10(raw))
    step = next(s * scale for s in STEPS if s * scale >= raw)
    top = math.ceil(peak / step - 1e-9) * step
    return top, [round(i * step, 10) for i in range(round(top / step) + 1)]


def tick_text(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:g}K"
    return f"{value:g}"


def tip(title: str, rows: Sequence[tuple[str, str, str]]) -> str:
    return esc(json.dumps({"t": title, "r": [list(r) for r in rows]}, ensure_ascii=False))


def legend(series: Sequence[Series], kind: str) -> str:
    if len(series) < 2:
        return ""
    items = "".join(
        f'<li><i class="{kind}" style="background:{s.color}"></i>{esc(s.label)}{s.hint}</li>'
        for s in series
    )
    return f'<ul class="lg">{items}</ul>'


def table_view(head: Sequence[str], rows: Sequence[Sequence[str]], t: Text) -> str:
    cols = "".join(f"<th>{esc(h)}</th>" for h in head)
    body = "".join(
        "<tr>"
        + "".join(
            f"<{'th' if i == 0 else 'td'}>{esc(c)}</{'th' if i == 0 else 'td'}>"
            for i, c in enumerate(r)
        )
        + "</tr>"
        for r in rows
    )
    return (
        f'<details class="tv"><summary>{esc(t("table"))}</summary>'
        f'<div class="tw"><table><thead><tr>{cols}</tr></thead><tbody>{body}</tbody></table></div></details>'
    )


def grid(top: float, ticks: Sequence[float], fmt: Fmt) -> list[str]:
    plot = HEIGHT - TOP - BOTTOM
    out = []
    for tick in ticks:
        y = TOP + plot - tick / top * plot
        css = "base" if tick == 0 else "grid"
        out.append(
            f'<line class="{css}" x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>'
        )
        out.append(
            f'<text class="ax" x="{LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">{esc(fmt(tick))}</text>'
        )
    return out


def bar_path(x: float, y: float, w: float, h: float, rounded: bool) -> str:
    r = min(CORNER, w / 2, h) if rounded else 0.0
    if r <= 0:
        return f"M{x:.1f},{y + h:.1f}V{y:.1f}H{x + w:.1f}V{y + h:.1f}Z"
    return (
        f"M{x:.1f},{y + h:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}"
        f"H{x + w - r:.1f}Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}V{y + h:.1f}Z"
    )


def columns(
    labels: Sequence[str],
    axis_labels: Sequence[str],
    series: Sequence[Series],
    values: Sequence[dict[str, float]],
    fmt: Fmt,
    tick: Fmt,
    aria: str,
    t: Text,
    day_head: str,
    total_head: str = "",
) -> Raw:
    totals = [sum(v.get(s.key, 0.0) for s in series) for v in values]
    top, ticks = nice_axis(max([*totals, 0.0]))
    plot = HEIGHT - TOP - BOTTOM
    slot = (WIDTH - LEFT - RIGHT) / max(1, len(values))
    bar_w = min(BAR_MAX, slot * 0.62)
    out = [
        f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{esc(aria)}">',
        *grid(top, ticks, tick),
    ]
    base = TOP + plot
    peak = max(range(len(values)), key=lambda i: totals[i]) if values else -1
    for i, vals in enumerate(values):
        x0 = LEFT + i * slot
        x = x0 + (slot - bar_w) / 2
        used = [s for s in series if vals.get(s.key, 0.0) > 0]
        rows = [(s.label, fmt(vals.get(s.key, 0.0)), s.color) for s in series]
        if len(series) > 1:
            rows.append((total_head, fmt(totals[i]), "transparent"))
        out.append(f'<g class="col" tabindex="0" data-tip="{tip(labels[i], rows)}">')
        out.append(f'<rect class="hit" x="{x0:.1f}" y="{TOP}" width="{slot:.1f}" height="{plot}"/>')
        cum = 0.0
        for k, s in enumerate(used):
            h = max(1.5, vals[s.key] / top * plot)
            y = base - cum / top * plot - h
            cum += vals[s.key]
            lower = k > 0
            out.append(
                f'<path class="seg" fill="{s.color}" d="{bar_path(x, y, bar_w, h - (BAR_GAP if lower else 0), k == len(used) - 1)}"/>'
            )
        out.append("</g>")
        if len(series) == 1 and i == peak and totals[i] > 0:
            ytxt = base - totals[i] / top * plot - 5
            out.append(
                f'<text class="val" x="{x + bar_w / 2:.1f}" y="{ytxt:.1f}" text-anchor="middle">{esc(fmt(totals[i]))}</text>'
            )
    for i, text in enumerate(axis_labels):
        if text:
            out.append(
                f'<text class="ax" x="{LEFT + (i + 0.5) * slot:.1f}" y="{HEIGHT - 8}" text-anchor="middle">{esc(text)}</text>'
            )
    out.append("</svg>")
    head = [day_head, *(s.label for s in series)] + ([total_head] if len(series) > 1 else [])
    rows_t = []
    for i in reversed(range(len(values))):
        cells = [labels[i], *(fmt(values[i].get(s.key, 0.0)) for s in series)]
        rows_t.append(cells + ([fmt(totals[i])] if len(series) > 1 else []))
    return Raw(
        f'<figure class="chart">{legend(series, "sw")}{"".join(out)}{table_view(head, rows_t, t)}</figure>'
    )


def line_points(xs: Sequence[float], ys: Sequence[float | None]) -> list[list[tuple[float, float]]]:
    runs: list[list[tuple[float, float]]] = []
    for x, y in zip(xs, ys, strict=True):
        if y is None:
            runs.append([])
        else:
            if not runs:
                runs.append([])
            runs[-1].append((x, y))
    return [r for r in runs if r]


def lines(
    labels: Sequence[str],
    series: Sequence[Series],
    values: Sequence[dict[str, float | None]],
    extra: Sequence[str],
    fmt: Fmt,
    aria: str,
    t: Text,
    head: Sequence[str],
    draw: bool,
) -> Raw:
    """Y axis is fixed at 0..1 (shares)."""
    plot = HEIGHT - TOP - BOTTOM
    slot = (WIDTH - LEFT - RIGHT) / max(1, len(values))
    xs = [LEFT + (i + 0.5) * slot for i in range(len(values))]

    def ypos(v: float) -> float:
        return TOP + plot - v * plot

    out = [f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{esc(aria)}">']
    out += grid(1.0, [0.0, 0.25, 0.5, 0.75, 1.0], fmt)
    if draw:
        out.append(f'<line class="xh" x1="0" x2="0" y1="{TOP}" y2="{TOP + plot}" hidden/>')
        for s in series:
            ys = [v.get(s.key) for v in values]
            for run in line_points(xs, ys):
                path = "M" + "L".join(f"{x:.1f},{ypos(y):.1f}" for x, y in run)
                out.append(f'<path class="ln" stroke="{s.color}" d="{path}"/>')
            for x, y in zip(xs, ys, strict=True):
                if y is not None:
                    out.append(
                        f'<circle class="dot" cx="{x:.1f}" cy="{ypos(y):.1f}" r="4" fill="{s.color}"/>'
                    )
        for k, s in enumerate(series):
            last = max((i for i, v in enumerate(values) if v.get(s.key) is not None), default=None)
            if last is not None:
                y = ypos(values[last][s.key] or 0.0) + (-10 if k == 0 else 17)
                out.append(
                    f'<text class="val" x="{xs[last]:.1f}" y="{y:.1f}" text-anchor="middle">{esc(fmt(values[last][s.key] or 0.0))}</text>'
                )
        for i, v in enumerate(values):
            rows = [(s.label, cell(v.get(s.key), fmt), s.color) for s in series]
            rows.append((extra[i], "", "transparent"))
            out.append(
                f'<g class="col" tabindex="0" data-x="{xs[i]:.1f}" data-tip="{tip(labels[i], rows)}">'
            )
            out.append(
                f'<rect class="hit" x="{LEFT + i * slot:.1f}" y="{TOP}" width="{slot:.1f}" height="{plot}"/></g>'
            )
    for i, text in enumerate(labels):
        out.append(
            f'<text class="ax" x="{xs[i]:.1f}" y="{HEIGHT - 8}" text-anchor="middle">{esc(text)}</text>'
        )
    out.append("</svg>")
    rows_t = [
        [labels[i], *(cell(v.get(s.key), fmt) for s in series), extra[i]]
        for i, v in reversed(list(enumerate(values)))
    ]
    return Raw(
        f'<figure class="chart">{legend(series, "ky")}{"".join(out)}{table_view(head, rows_t, t)}</figure>'
    )


def cell(value: float | None, fmt: Fmt) -> str:
    return "—" if value is None else fmt(value)


def axis_labels(days: Sequence[str], label: Callable[[str], str]) -> list[str]:
    last = len(days) - 1
    return [label(d) if (last - i) % LABEL_EVERY == 0 else "" for i, d in enumerate(days)]
