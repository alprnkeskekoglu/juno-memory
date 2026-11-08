from collections.abc import Mapping
from typing import Any

NO_DATA = "no data"
SHOWN = 5


def num(value: float | None, pattern: str = "{:.1f}") -> str:
    return NO_DATA if value is None else pattern.format(value)


def line(label: str, value: object) -> str:
    return f"- {label}: {value}"


def signal_lines(s: Mapping[str, Any]) -> list[str]:
    c, r, p, k = s["corrections"], s["recommended_pick"], s["pushback"], s["card_cost"]
    out = [
        line(
            "corrections per 100 prompts",
            NO_DATA
            if c is None
            else f"{num(c['per_100'])} ({c['cues']} of {c['prompts']} prompts"
            + (f"; last week {num(c['prev_per_100'])}" if c["prev_per_100"] is not None else "")
            + ")",
        ),
        line(
            "Recommended-pick rate",
            NO_DATA if r is None else f"{r['rate']:.0%} ({r['picked']} of {r['asks']} answers)",
        ),
        line(
            "pushback per 100 proposals",
            NO_DATA
            if p is None
            else f"{num(p['per_100'])} ({p['pushback']} of {p['proposals']} proposals)",
        ),
        line(
            "card cost",
            NO_DATA
            if k is None
            else f"${k['per_run']:.4f} per run ({k['runs']} runs), ${k['per_day']:.4f} per day "
            f"({k['days']} days), ${k['usd']:.4f} total",
        ),
    ]
    sections = s["sections"]
    out.append(
        line(
            "dropped extraction sections",
            NO_DATA
            if sections is None
            else "; ".join(f"{k} {v['dropped']}/{v['runs']}" for k, v in sorted(sections.items())),
        )
    )
    errors = s["hook_errors"]
    out.append(
        line(
            "hook errors per event",
            NO_DATA
            if errors is None
            else "none"
            if not errors
            else ", ".join(f"{e} {n}" for e, n in sorted(errors.items())),
        )
    )
    writes = s["writes"]
    out.append(
        line(
            "writes (undone) per verb",
            NO_DATA
            if writes is None
            else ", ".join(f"{v} {n['writes']} ({n['undone']})" for v, n in sorted(writes.items())),
        )
    )
    repeats = s["pitfall_repeats"]
    out.append(
        line(
            "repeated pitfall triggers",
            NO_DATA
            if repeats is None
            else "none repeated"
            if not repeats
            else "; ".join(f"{x['text']} ({x['sessions']} sessions)" for x in repeats[:SHOWN]),
        )
    )
    return out


def retrieval_lines(r: Mapping[str, Any]) -> list[str]:
    recall, paired = r["recall"], r["paired"]
    out = [
        line(
            "recall proxy (search only)",
            NO_DATA
            if recall is None
            else f"{recall['hits']}/{recall['with_evidence']} entries with evidence, "
            f"with packs {recall['pack_hits']}/{recall['with_evidence']}",
        )
    ]
    out.append(
        line(
            "paired comparison, keys vs none (alt)",
            NO_DATA
            if paired is None
            else f"{paired['n']} entries: keys win {paired['only_w']}, no-keys win "
            f"{paired['only_0']}, both {paired['both']}, neither {paired['neither']}, "
            f"sign test p={paired['p']}",
        )
    )
    return out


def arm_lines(arms: list[Mapping[str, Any]] | None) -> list[str]:
    if arms is None:
        return [line("arms", NO_DATA)]
    out = []
    for a in arms:
        n = a["n"]
        counts = NO_DATA if n is None else f"on {n.get('on', '-')} / off {n.get('off', '-')}"
        days = f", day {a['days']} of {a['max_days']}" if a["days"] is not None else ""
        fired = ", stop rule fired" if a["stop_fired"] else ""
        out.append(
            line(
                f"{a['name']} ({a['status']})",
                f"n {counts} against min_n {a['min_n']}{days}{fired}",
            )
        )
        out.append(f"  - stop rule: {a['stop_rule']}")
    return out


def memory_lines(m: Mapping[str, Any]) -> list[str]:
    keys, decay, inbox, dups = m["keys"], m["decay"], m["inbox"], m["duplicates"]
    out = [
        line(
            "keys coverage",
            NO_DATA
            if keys is None
            else f"{keys['keyed']}/{keys['total']} passages keyed, {keys['pending']} pending, "
            f"{keys['empty']} without usable facts",
        )
    ]
    if decay is None:
        out.append(line("decay candidates", NO_DATA))
    else:
        out.append(
            line(
                "decay candidates",
                f"{len(decay['candidates'])} of {decay['checked']} files without a signal in "
                f"{decay['window_days']} days",
            )
        )
        out += [f"  - {c['path']}" for c in decay["candidates"][:SHOWN]]
    out.append(
        line(
            "inbox",
            NO_DATA
            if inbox is None
            else f"{inbox['items']} items"
            + (f", oldest {inbox['oldest_days']} days" if inbox["oldest_days"] is not None else ""),
        )
    )
    out.append(line("cross-file duplicate groups", len(dups) if dups is not None else NO_DATA))
    out += [f"  - {', '.join(g['files'])}: {g['text']}" for g in (dups or [])[:SHOWN]]
    return out


def proposal_lines(items: list[Mapping[str, Any]]) -> list[str]:
    if not items:
        return ["- none"]
    return [f"- Level {p['level']}: {p['text']}" for p in items]


def render(report: Mapping[str, Any]) -> str:
    parts = [
        ("## Signals", signal_lines(report["signals"])),
        ("## Retrieval", retrieval_lines(report["retrieval"])),
        ("## Arms", arm_lines(report["arms"])),
        ("## Memory", memory_lines(report["memory"])),
        ("## Pending proposals", proposal_lines(report["proposals"])),
    ]
    head = f"Generated by Juno on {report['generated']}; deterministic, no model call. Do not edit."
    text = f"# Weekly report {report['week']}\n\n{head}\n"
    for title, lines in parts:
        text += f"\n{title}\n\n" + "\n".join(lines) + "\n"
    return text
