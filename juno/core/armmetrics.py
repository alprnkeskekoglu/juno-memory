import random
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any

from juno.core import fold, selfmodel

Outcome = dict[str, Any]
Metric = Callable[[str, Mapping[str, Any]], Outcome]


def segments(name: str, data: Mapping[str, Any]) -> Outcome:
    entries: Sequence[Mapping[str, Any]] = data["shadow"]
    has_cue: Callable[[str], bool] = data["has_cue"]
    seen: dict[str, dict[str, Any]] = {}
    for entry in entries:
        arms = entry.get("arms")
        side = arms.get(name) if isinstance(arms, dict) else None
        if side not in ("on", "off"):
            continue
        seg = seen.setdefault(str(entry["segment"]), {"side": side, "prompts": 0, "cues": 0})
        seg["prompts"] += 1
        seg["cues"] += has_cue(entry["prompt"])
    n = {"on": 0, "off": 0}
    totals = {side: {"prompts": 0, "cues": 0} for side in n}
    for seg in seen.values():
        n[seg["side"]] += 1
        totals[seg["side"]]["prompts"] += seg["prompts"]
        totals[seg["side"]]["cues"] += seg["cues"]
    estimate: dict[str, dict[str, float | None]] = {}
    for side, t in totals.items():
        estimate[side] = {
            "prompts_per_segment": t["prompts"] / n[side] if n[side] else None,
            "cues_per_100_prompts": 100 * t["cues"] / t["prompts"] if t["prompts"] else None,
        }
    return {"n": n, "stop_fired": False, "estimate": estimate}


HARM_MIN_UNITS = 30
HARM_RATIO = 1.5


def read_after(
    packs: Sequence[tuple[float, Collection[str]]], touches: Sequence[tuple[float, str]]
) -> bool:
    return any(ts >= stamp and rel in nb for stamp, nb in packs for ts, rel in touches)


def linked_read_share(name: str, data: Mapping[str, Any]) -> Outcome:
    n = {"on": 0, "off": 0}
    read = {"on": 0, "off": 0}
    for row in data.get("rows", {}).values():
        counts = (row.get("counters") or {}).get(name) or {}
        side = (row.get("arms") or {}).get(name)
        if side in n and counts.get("nb_units") and counts.get("nb_read") is not None:
            n[side] += 1
            read[side] += bool(counts["nb_read"])
    units: dict[str, list[tuple[float, set[str]]]] = {}
    for sid, stamp, nb in data["packs"]():
        if nb:
            units.setdefault(sid, []).append((stamp, set(nb)))
    for sid, packs in units.items():
        side = data["side_of"](sid)
        touches = data["reads"](sid)
        if side not in n or touches is None:
            continue
        n[side] += 1
        read[side] += read_after(packs, touches)
    secondary = segments(name, data)["estimate"] if "shadow" in data else {}
    cue_on = (secondary.get("on") or {}).get("cues_per_100_prompts")
    cue_off = (secondary.get("off") or {}).get("cues_per_100_prompts")
    harm = (
        min(n.values()) >= HARM_MIN_UNITS
        and cue_on is not None
        and bool(cue_off)
        and cue_on > HARM_RATIO * (cue_off or 0)
    )
    estimate = {
        side: {"read_share": read[side] / n[side] if n[side] else None, "units": n[side]}
        for side in n
    }
    return {"n": n, "stop_fired": harm, "estimate": estimate, "secondary": secondary}


HIT_PROMPTS = 3
HIT_JACCARD = 0.5
BOOTSTRAP_DRAWS = 2000


def predicted(questions: Sequence[str], typed: Sequence[str], preset: str) -> bool:
    def words(text: str) -> set[str]:
        return set(re.findall(r"\w+", fold.fold(text, preset)))

    asked = [words(q) for q in questions]
    for prompt in typed[:HIT_PROMPTS]:
        got = words(prompt)
        for q in asked:
            if q and got and len(q & got) / len(q | got) >= HIT_JACCARD:
                return True
    return False


def upper90_of_difference(on: Sequence[int], off: Sequence[int]) -> float | None:
    if not on or not off:
        return None
    rng = random.Random(0)
    diffs = sorted(
        sum(rng.choices(on, k=len(on))) / len(on) - sum(rng.choices(off, k=len(off))) / len(off)
        for _ in range(BOOTSTRAP_DRAWS)
    )
    return diffs[int(0.9 * (BOOTSTRAP_DRAWS - 1))]


def next_lookup_calls(name: str, data: Mapping[str, Any]) -> Outcome:
    n = {"on": 0, "off": 0}
    lookups: dict[str, list[int]] = {"on": [], "off": []}
    hits = {"on": 0, "off": 0}
    for row in data.get("rows", {}).values():
        counts = (row.get("counters") or {}).get(name) or {}
        side = (row.get("arms") or {}).get(name)
        if side not in n or not counts.get("units"):
            continue
        n[side] += 1
        hits[side] += bool(counts.get("hit"))
        if counts.get("lookups") is not None:
            lookups[side].append(int(counts["lookups"]))
    estimate: dict[str, dict[str, float | int | None]] = {
        side: {
            "lookups_per_unit": sum(lookups[side]) / len(lookups[side]) if lookups[side] else None,
            "hit_rate": hits[side] / n[side] if n[side] else None,
            "units": n[side],
        }
        for side in n
    }
    upper = upper90_of_difference(lookups["on"], lookups["off"])
    return {"n": n, "stop_fired": False, "estimate": estimate, "diff_upper90": upper}


METRICS: dict[str, Metric] = {
    "segments": segments,
    "linked_read_share": linked_read_share,
    "next_lookup_calls": next_lookup_calls,
}


PUSHBACK_MIN_PROPOSALS = 75
PUSHBACK_RATIO = 0.7
BOOTSTRAPS = 2000
SELF_MIN_QUESTIONS = 450
SELF_MIN_SEGMENTS = 40
SELF_MIN_PROPOSALS = 150
SELF_COUNTS = ("asks", "rec", "proposals", "pushback")


def self_segments(name: str, data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for sid, row in data.get("rows", {}).items():
        side = (row.get("arms") or {}).get(name)
        counts = (row.get("counters") or {}).get(name)
        if side in ("on", "off") and isinstance(counts, dict):
            found[sid] = {"side": side, **{k: int(counts.get(k) or 0) for k in SELF_COUNTS}}
    live: dict[str, dict[str, Any]] = {}

    def seg(row: Mapping[str, Any]) -> dict[str, Any] | None:
        side = (row.get("arms") or {}).get(name)
        if row["sid"] in found or side not in ("on", "off"):
            return None
        return live.setdefault(row["sid"], {"side": side, **dict.fromkeys(SELF_COUNTS, 0)})

    for row in data["asks"]() if "asks" in data else ():
        target = seg(row) if selfmodel.is_unit(row) else None
        if target is not None:
            target["asks"] += 1
            target["rec"] += selfmodel.took_rec(row)
    for row in data["runs"]() if "runs" in data else ():
        turns = row.get("turns")
        target = seg(row) if isinstance(turns, dict) else None
        if target is not None:
            target["proposals"] = max(target["proposals"], int(turns.get("proposals") or 0))
            target["pushback"] = max(target["pushback"], int(turns.get("pushback") or 0))
    return found | live


def pooled(clusters: Sequence[tuple[int, int]]) -> float | None:
    total = sum(n for n, _ in clusters)
    return sum(k for _, k in clusters) / total if total else None


def cluster_gain(
    on: Sequence[tuple[int, int]], off: Sequence[tuple[int, int]]
) -> tuple[float | None, float | None]:
    point_on, point_off = pooled(on), pooled(off)
    if point_on is None or point_off is None:
        return None, None
    rng = random.Random(0)
    gains: list[float] = []
    for _ in range(BOOTSTRAPS):
        a = pooled(rng.choices(on, k=len(on)))
        b = pooled(rng.choices(off, k=len(off)))
        if a is not None and b is not None:
            gains.append(a - b)
    gains.sort()
    return point_on - point_off, gains[int(0.05 * len(gains))] if gains else None


def self_guard(segs: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Any], bool]:
    proposals = {"on": 0, "off": 0}
    pushback = {"on": 0, "off": 0}
    for seg in segs.values():
        proposals[seg["side"]] += seg["proposals"]
        pushback[seg["side"]] += seg["pushback"]
    rate = {
        side: 100 * pushback[side] / proposals[side] if proposals[side] else None
        for side in proposals
    }
    stop = (
        min(proposals.values()) >= PUSHBACK_MIN_PROPOSALS
        and rate["on"] is not None
        and rate["off"] is not None
        and rate["on"] < PUSHBACK_RATIO * rate["off"]
    )
    return {"proposals": proposals, "pushback_per_100": rate}, stop


def pushback_rate(name: str, data: Mapping[str, Any]) -> Outcome:
    guard, stop = self_guard(self_segments(name, data))
    short = min(guard["proposals"].values()) < SELF_MIN_PROPOSALS
    return {
        "n": guard["proposals"],
        "stop_fired": stop,
        "ready": not short,
        "reason": f"needs {SELF_MIN_PROPOSALS} proposals per arm" if short else "",
        "estimate": {s: {"pushback_per_100": guard["pushback_per_100"][s]} for s in ("on", "off")},
    }


def recommended_pick(name: str, data: Mapping[str, Any]) -> Outcome:
    segs = self_segments(name, data)
    clusters: dict[str, list[tuple[int, int]]] = {"on": [], "off": []}
    for seg in segs.values():
        if seg["asks"]:
            clusters[seg["side"]].append((seg["asks"], seg["rec"]))
    gain, lower = cluster_gain(clusters["on"], clusters["off"])
    guard, stop = self_guard(segs)
    n = {side: sum(q for q, _ in rows) for side, rows in clusters.items()}
    missing = [
        text
        for text, short in (
            (f"{SELF_MIN_QUESTIONS} questions", min(n.values()) < SELF_MIN_QUESTIONS),
            (
                f"{SELF_MIN_SEGMENTS} segments",
                min(len(rows) for rows in clusters.values()) < SELF_MIN_SEGMENTS,
            ),
            (
                f"{SELF_MIN_PROPOSALS} proposals",
                min(guard["proposals"].values()) < SELF_MIN_PROPOSALS,
            ),
        )
        if short
    ]
    return {
        "n": n,
        "stop_fired": stop,
        "ready": not missing,
        "reason": f"needs {', '.join(missing)} per arm" if missing else "",
        "estimate": {
            side: {"pick_rate": pooled(rows), "segments": len(rows)}
            for side, rows in clusters.items()
        },
        "gain": gain,
        "lower90": lower,
        "guard": guard,
    }


METRICS["recommended_pick"] = recommended_pick
METRICS["pushback_rate"] = pushback_rate
