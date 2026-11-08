import datetime
import hashlib
import math
import random
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from juno.core import prompts

SEARCH_LIMIT = 10
LABELS = ("useful", "not")
WILSON_MIN = 0.5
ABSTENTION_MIN = 0.9
HARD_NEG_MIN = 20
RECALL_READ_TOOLS = ("Read",)
RECALL_WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
RECALL_SKIP = ("core.md", "protocol/", "rules/")
RECALL_MISSES_SHOWN = 20
AB_ARMS = ("cards", "holdout")
TICKET_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]{0,9}-[0-9]+\b")

Entry = dict[str, Any]
Touch = tuple[float, str, str]
Run = tuple[Entry, list[dict[str, Any]], float]
Touches = Callable[[str], list[Touch] | None]


class SetTamperedError(ValueError):
    pass


def entry_id(ts: object, segment: object, prompt: object) -> str:
    raw = f"{ts}\0{segment}\0{prompt}".encode()
    return hashlib.sha256(raw).hexdigest()[:12]


def sample_pool(
    entries: Sequence[Entry], excluded: set[str], since: float | None
) -> dict[str, list[Entry]]:
    pool: dict[str, list[Entry]] = {}
    seen: set[str] = set()
    for e in sorted(entries, key=lambda e: (e["ts"], str(e["segment"]), e["prompt"])):
        eid = entry_id(e["ts"], e["segment"], e["prompt"])
        if eid in excluded or eid in seen or (since is not None and e["ts"] < since):
            continue
        seen.add(eid)
        pool.setdefault(str(e["segment"]), []).append(
            {
                "id": eid,
                "ts": e["ts"],
                "segment": str(e["segment"]),
                "prompt": e["prompt"],
                "suggestions": e.get("suggestions", []),
                "label": None,
                "prefill": None,
                "forbidden": [],
                "hard_negative": False,
            }
        )
    return pool


def pick_sample(pool: dict[str, list[Entry]], n: int, seed: int) -> list[Entry]:
    rng = random.Random(seed)
    segs = sorted(pool)
    rng.shuffle(segs)
    for s in segs:
        rng.shuffle(pool[s])
    picked: list[Entry] = []
    while len(picked) < n and any(pool.values()):
        for s in segs:
            if pool[s] and len(picked) < n:
                picked.append(pool[s].pop())
    return picked


def check_labels(entries: Sequence[Entry], field: str = "label") -> None:
    for e in entries:
        if e.get(field) not in (None, *LABELS):
            raise ValueError(f"entry {e.get('id')}: invalid {field} {e.get(field)!r}")


def wilson_lower(successes: float, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    p = successes / n
    z2 = z * z
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (centre - margin) / (1 + z2 / n)


def p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


def logged_results(entry: Entry) -> list[dict[str, Any]]:
    return [
        {"relpath": s["path"], "score": s.get("score", 0)} for s in entry.get("suggestions", [])
    ]


def metrics(runs: Sequence[Run], floor: float, retired: set[str]) -> dict[str, Any]:
    fired = useful = unlabelled = hn = abstained = 0
    forbidden: list[dict[str, str]] = []
    for e, results, _ in runs:
        hits = [r for r in results if r["score"] >= floor]
        bad = set(e.get("forbidden") or []) | retired
        paths = {r["relpath"] for r in hits}
        forbidden += [{"id": e["id"], "path": p} for p in sorted(paths & bad)]
        if e.get("hard_negative"):
            hn += 1
            abstained += 0 if hits else 1
            continue
        if not hits:
            continue
        if e.get("label") is None:
            unlabelled += 1
            continue
        fired += 1
        useful += e["label"] == "useful"
    return {
        "floor": floor,
        "fired": fired,
        "useful": useful,
        "unlabelled": unlabelled,
        "precision": (useful / fired) if fired else None,
        "wilson_lower": wilson_lower(useful, fired),
        "forbidden_hits": forbidden,
        "n_hard_negatives": hn,
        "abstained": abstained,
        "abstention": (abstained / hn) if hn else None,
    }


def evaluate_runs(runs: Sequence[Run], floor: float, retired: set[str]) -> dict[str, Any]:
    m = metrics(runs, floor, retired)
    m["n_entries"] = len(runs)
    m["p95_ms"] = p95([ms for _, _, ms in runs])
    return m


def fmt(value: float | None, pattern: str = "{:.3f}") -> str:
    return "n/a" if value is None else pattern.format(value)


def report(m: Mapping[str, Any], name: str = "Juno") -> str:
    lines = [
        f"# {name} eval at floor {m['floor']}",
        f"- entries: {m['n_entries']}; fired and labelled: {m['fired']}; "
        f"fired but unlabelled: {m['unlabelled']}",
        f"- precision: {fmt(m['precision'])} ({m['useful']}/{m['fired']}), "
        f"Wilson 95% lower bound {fmt(m['wilson_lower'])}",
        f"- forbidden hits: {len(m['forbidden_hits'])}",
        f"- hard negatives: {m['n_hard_negatives']}; abstention {fmt(m['abstention'])}",
        f"- search p95: {fmt(m['p95_ms'], '{:.1f}')} ms",
    ]
    return "\n".join(lines) + "\n"


def best_floor(runs: Sequence[Run], retired: set[str]) -> float:
    candidates = sorted({r["score"] for _, res, _ in runs for r in res})
    best: tuple[float, float] | None = None
    for c in candidates:
        m = metrics(runs, c, retired)
        if m["abstention"] is None or m["abstention"] < ABSTENTION_MIN or m["precision"] is None:
            continue
        if best is None or m["precision"] > best[0]:
            best = (m["precision"], c)
    if best is None:
        raise ValueError("no floor reaches abstention >= 0.9 with a labelled precision on this set")
    return best[1]


def gate_reasons(m: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if m["unlabelled"]:
        reasons.append(f"{m['unlabelled']} fired entries are unlabelled")
    if m["fired"] == 0:
        reasons.append("no labelled entry fired a suggestion at this floor")
    elif m["wilson_lower"] < WILSON_MIN:
        reasons.append(f"Wilson lower bound {m['wilson_lower']:.3f} < {WILSON_MIN}")
    if m["forbidden_hits"]:
        reasons.append(f"{len(m['forbidden_hits'])} forbidden (retired) hits, must be 0")
    if m["n_hard_negatives"] < HARD_NEG_MIN:
        reasons.append(f"only {m['n_hard_negatives']} hard negatives, need >= {HARD_NEG_MIN}")
    elif m["abstention"] < ABSTENTION_MIN:
        reasons.append(f"abstention {m['abstention']:.3f} < {ABSTENTION_MIN}")
    return reasons


def agreement(entries: Sequence[Entry]) -> dict[str, Any]:
    check_labels(entries, "label")
    check_labels(entries, "prefill")
    pairs = [
        (e["id"], e["label"], e["prefill"])
        for e in entries
        if e.get("label") is not None and e.get("prefill") is not None
    ]
    if not pairs:
        raise ValueError("no entry has both label and prefill")
    n = len(pairs)
    observed = sum(a == b for _, a, b in pairs) / n
    expected = sum(
        (sum(a == c for _, a, _ in pairs) / n) * (sum(b == c for _, _, b in pairs) / n)
        for c in LABELS
    )
    kappa = None if expected >= 1 else (observed - expected) / (1 - expected)
    return {
        "n": n,
        "one_side_only": sum(
            (e.get("label") is None) != (e.get("prefill") is None) for e in entries
        ),
        "observed": observed,
        "expected": expected,
        "kappa": kappa,
        "disagreements": [{"id": i, "label": a, "prefill": b} for i, a, b in pairs if a != b],
    }


def agreement_report(m: Mapping[str, Any], name: str = "Juno") -> str:
    kappa = "n/a (one class only)" if m["kappa"] is None else f"{m['kappa']:.3f}"
    lines = [
        f"# {name} label agreement (label vs prefill)",
        f"- pairs: {m['n']}; entries with only one of the two: {m['one_side_only']}",
        f"- observed agreement: {m['observed']:.3f}; chance: {m['expected']:.3f}; "
        f"Cohen's kappa: {kappa}",
    ]
    lines += [
        f"  - {d['id']}: label {d['label']}, prefill {d['prefill']}" for d in m["disagreements"]
    ]
    return "\n".join(lines) + "\n"


def iso_ts(value: object) -> float | None:
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def pasted_keys(entry: Mapping[str, Any]) -> set[str]:
    if isinstance(entry.get("pasted"), list):
        return {str(k).upper() for k in entry["pasted"]}
    prompt = entry["prompt"]
    inside = {
        m.group(0).upper()
        for block in prompts.PASTED_CONTENT_RE.findall(prompt)
        for m in TICKET_RE.finditer(block)
    }
    outside = {
        m.group(0).upper() for m in TICKET_RE.finditer(prompts.PASTED_CONTENT_RE.sub("", prompt))
    }
    return inside - outside


def needed_files(
    entry: Entry, end: float, touches: Sequence[Touch], ticket_exists: Callable[[str], bool]
) -> dict[str, str]:
    window = [(tool, rel) for ts, tool, rel in touches if entry["ts"] <= ts < end]
    written = {rel for tool, rel in window if tool in RECALL_WRITE_TOOLS}
    needed: dict[str, str] = {}
    for tool, rel in window:
        if tool in RECALL_READ_TOOLS and rel not in written and not rel.startswith(RECALL_SKIP):
            needed.setdefault(rel, "read")
    keys = [m.group(0).upper() for m in TICKET_RE.finditer(entry["prompt"])]
    for key in keys + sorted(pasted_keys(entry)):
        rel = f"tasks/{key}.md"
        if ticket_exists(rel):
            needed.setdefault(rel, "ticket")
    return needed


def miss_kind(needed: Mapping[str, str], entry: Entry, suggested: Sequence[str]) -> str:
    pasted = {f"tasks/{k}.md" for k in pasted_keys(entry)}
    if pasted and set(needed) <= pasted:
        return "paste"
    return "wrong" if suggested else "silent"


def evidence_entries(
    entries: Sequence[Entry], touches_for: Touches, ticket_exists: Callable[[str], bool]
) -> list[tuple[Entry, set[str]]]:
    by_segment: dict[str, list[Entry]] = {}
    for e in sorted(entries, key=lambda e: (str(e["segment"]), e["ts"])):
        by_segment.setdefault(str(e["segment"]), []).append(e)
    found: list[tuple[Entry, set[str]]] = []
    for segment, items in sorted(by_segment.items()):
        touches = touches_for(segment) or []
        for i, e in enumerate(items):
            end = items[i + 1]["ts"] if i + 1 < len(items) else float("inf")
            needed = needed_files(e, end, touches, ticket_exists)
            if needed:
                found.append((e, set(needed)))
    return found


def compare_report(m: Mapping[str, Any]) -> str:
    lines = [
        f"# Paired comparison, setting {m['setting']}: {m['n']} entries with evidence, "
        f"weight 0 hits {m['hits'].get('0', 0)}"
    ]
    for weight, v in m["weights"].items():
        lines.append(
            f"- weight {weight}: only-{weight} {v['only_w']}, only-0 {v['only_0']}, "
            f"both {v['both']}, neither {v['neither']}, sign test p={v['p']}"
        )
    return "\n".join(lines) + "\n"


def recall_from(
    entries: Sequence[Entry],
    touches_for: Touches,
    ticket_exists: Callable[[str], bool],
    floor: float,
) -> dict[str, Any]:
    by_segment: dict[str, list[Entry]] = {}
    for e in sorted(entries, key=lambda e: (str(e["segment"]), e["ts"])):
        by_segment.setdefault(str(e["segment"]), []).append(e)
    counts = {
        "entries": len(entries),
        "with_evidence": 0,
        "hits": 0,
        "pack_hits": 0,
        "silent": 0,
        "wrong": 0,
        "paste": 0,
        "no_transcript": 0,
        "with_pack_data": sum(isinstance(e.get("delivered"), list) for e in entries),
    }
    sources = {"read": [0, 0, 0], "ticket": [0, 0, 0]}
    misses: list[dict[str, Any]] = []
    for segment, items in sorted(by_segment.items()):
        found_touches = touches_for(segment)
        touches = found_touches or []
        if found_touches is None:
            counts["no_transcript"] += len(items)
        for i, e in enumerate(items):
            end = items[i + 1]["ts"] if i + 1 < len(items) else float("inf")
            needed = needed_files(e, end, touches, ticket_exists)
            if not needed:
                continue
            counts["with_evidence"] += 1
            suggested = [s["path"] for s in e.get("suggestions", []) if s.get("score", 0) >= floor]
            packed = set(e.get("delivered") or []) | set(e.get("carried") or [])
            found = set(needed) & set(suggested)
            found_packs = set(needed) & (set(suggested) | packed)
            for rel, source in needed.items():
                sources[source][0] += 1
                sources[source][1] += rel in found
                sources[source][2] += rel in found_packs
            counts["hits"] += bool(found)
            if found_packs:
                counts["pack_hits"] += 1
                continue
            kind = miss_kind(needed, e, suggested)
            counts[kind] += 1
            misses.append(
                {
                    "id": entry_id(e["ts"], e["segment"], e["prompt"]),
                    "ts": e["ts"],
                    "kind": kind,
                    "prompt": e["prompt"][:80],
                    "needed": sorted(needed),
                    "suggested": suggested[:3],
                }
            )
    n = counts["with_evidence"]
    return dict(
        counts,
        floor=floor,
        recall=(counts["hits"] / n) if n else None,
        wilson_lower=wilson_lower(counts["hits"], n),
        pack_recall=(counts["pack_hits"] / n) if n else None,
        pack_wilson_lower=wilson_lower(counts["pack_hits"], n),
        by_source={
            k: {"needed": v[0], "suggested": v[1], "with_packs": v[2]} for k, v in sources.items()
        },
        misses=sorted(misses, key=lambda m: m["ts"], reverse=True),
    )


def recall_report(m: Mapping[str, Any], name: str = "Juno") -> str:
    lines = [
        f"# {name} recall proxy at floor {m['floor']}",
        f"- shadow entries: {m['entries']}; with evidence: {m['with_evidence']}; "
        f"no transcript: {m['no_transcript']}",
        f"- recall proxy (search only): {fmt(m['recall'])} ({m['hits']}/{m['with_evidence']}), "
        f"Wilson 95% lower bound {fmt(m['wilson_lower'])}",
        f"- recall proxy (search + packs): {fmt(m['pack_recall'])} "
        f"({m['pack_hits']}/{m['with_evidence']}), Wilson 95% lower bound "
        f"{fmt(m['pack_wilson_lower'])}; {m['with_pack_data']} entries record packs",
        f"- misses after packs: {m['silent']} with nothing suggested, {m['wrong']} with other "
        f"files suggested, {m['paste']} with the ticket only in pasted text",
    ]
    for source, v in sorted(m["by_source"].items()):
        lines.append(
            f"- {source}: {v['suggested']}/{v['needed']} needed files suggested, "
            f"{v['with_packs']} with packs"
        )
    for miss in m["misses"][:RECALL_MISSES_SHOWN]:
        lines.append(
            f"  - {miss['id']} {miss['kind']}: {miss['prompt']!r} needed "
            f"{', '.join(miss['needed'][:3])}"
        )
    if len(m["misses"]) > RECALL_MISSES_SHOWN:
        lines.append(f"  - {len(m['misses']) - RECALL_MISSES_SHOWN} more misses not shown")
    return "\n".join(lines) + "\n"


def card_ab_from(
    entries: Sequence[Entry], touches_for: Touches, has_cue: Callable[[str], bool]
) -> dict[str, Any]:
    segments: dict[str, dict[str, Any]] = {}
    for e in entries:
        if e.get("arm") in AB_ARMS:
            seg = segments.setdefault(str(e["segment"]), {"arm": e["arm"], "prompts": 0, "cues": 0})
            seg["prompts"] += 1
            seg["cues"] += has_cue(e["prompt"])
    arms: dict[str, dict[str, Any]] = {
        a: {"segments": 0, "prompts": 0, "cues": 0, "card_reads": 0, "no_transcript": 0}
        for a in AB_ARMS
    }
    for segment, seg in sorted(segments.items()):
        m = arms[seg["arm"]]
        m["segments"] += 1
        m["prompts"] += seg["prompts"]
        m["cues"] += seg["cues"]
        touches = touches_for(segment)
        if touches is None:
            m["no_transcript"] += 1
            continue
        reads = [
            rel
            for _, tool, rel in touches
            if tool in RECALL_READ_TOOLS and rel.startswith("handoffs/")
        ]
        m["card_reads"] += bool(reads)
    for m in arms.values():
        n, seen = m["segments"], m["segments"] - m["no_transcript"]
        m["prompts_per_segment"] = m["prompts"] / n if n else None
        m["cues_per_100_prompts"] = 100 * m["cues"] / m["prompts"] if m["prompts"] else None
        m["card_read_share"] = m["card_reads"] / seen if seen else None
        m["card_read_wilson_lower"] = wilson_lower(m["card_reads"], seen)
    return {"segments": len(segments), "arms": arms}
