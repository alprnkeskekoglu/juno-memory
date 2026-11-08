import datetime
import difflib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from juno.core import fold, keys, selfmodel, writegate

DATE_END_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*$")
DAY = 86400
RISE = 1.4
MIN_PROMPTS = 20
MIN_RUNS = 5
PAIRED_P = 0.05
DUP_MIN_CHARS = 20
DUP_MAX_LINES = 3000
DUP_FILE_KINDS = ("knowledge", "decisions", "projects", "self", "procedures", "notes", "ideas")
Raw = Mapping[str, Any]


def per100(count: int, total: int) -> float | None:
    return 100 * count / total if total else None


def corrections(raw: Raw) -> dict[str, Any] | None:
    cues, prompts = raw["cues"], raw["prompts"]
    if not prompts:
        return None
    before = per100(raw["prev_cues"], raw["prev_prompts"])
    return {
        "cues": cues,
        "prompts": prompts,
        "per_100": per100(cues, prompts),
        "prev_per_100": before,
    }


def recommended_pick(asks: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    units = [row for row in asks if selfmodel.is_unit(row)]
    if not units:
        return None
    picked = sum(1 for row in units if selfmodel.took_rec(row))
    return {"picked": picked, "asks": len(units), "rate": picked / len(units)}


def pushback(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    per_segment: dict[str, tuple[int, int]] = {}
    for row in runs:
        turns = row.get("turns")
        if isinstance(turns, dict):
            seen = per_segment.get(str(row.get("sid")), (0, 0))
            per_segment[str(row.get("sid"))] = (
                max(seen[0], int(turns.get("proposals") or 0)),
                max(seen[1], int(turns.get("pushback") or 0)),
            )
    proposals = sum(p for p, _ in per_segment.values())
    if not proposals:
        return None
    count = sum(b for _, b in per_segment.values())
    return {"pushback": count, "proposals": proposals, "per_100": per100(count, proposals)}


def card_cost(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not runs:
        return None
    total = sum(float(r.get("cost_usd") or 0) for r in runs)
    days = {datetime.date.fromtimestamp(float(r["t"])) for r in runs}
    return {
        "usd": total,
        "runs": len(runs),
        "per_run": total / len(runs),
        "days": len(days),
        "per_day": total / len(days),
    }


def section_drops(runs: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]] | None:
    found: dict[str, dict[str, int]] = {}
    for row in runs:
        sections = row.get("sections")
        for kind, status in sections.items() if isinstance(sections, dict) else ():
            text = str(status)
            if text == "none":
                continue
            seen = found.setdefault(str(kind), {"dropped": 0, "runs": 0})
            seen["runs"] += 1
            seen["dropped"] += text != "ok"
    return found or None


def write_counts(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]] | None:
    found: dict[str, dict[str, int]] = {}
    for rec in records:
        if rec.get("verb") == "undo":
            if rec.get("undid"):
                found.setdefault(str(rec["undid"]), {"writes": 0, "undone": 0})["undone"] += 1
        else:
            found.setdefault(str(rec.get("verb")), {"writes": 0, "undone": 0})["writes"] += 1
    return found or None


def open_sections(core_text: str) -> list[str]:
    names: list[str] = []
    inside = fenced = False
    for line in core_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
        elif stripped == "<!-- juno:invariant -->":
            inside = True
        elif stripped == "<!-- /juno:invariant -->":
            inside = False
        elif line.startswith("## ") and not inside and not fenced:
            names.append(line[3:].strip())
    return names


def inbox_age(text: str, now: float) -> dict[str, Any]:
    days: list[int] = []
    items = 0
    for line in text.splitlines():
        if not line.startswith("- "):
            continue
        items += 1
        found = DATE_END_RE.search(line)
        if found:
            day = datetime.date.fromisoformat(found[1])
            days.append((datetime.date.fromtimestamp(now) - day).days)
    return {"items": items, "oldest_days": max(days) if days else None}


def duplicate_groups(files: Mapping[str, str], preset: str) -> list[dict[str, Any]]:
    lines: list[tuple[str, str, str]] = []
    for rel, text in sorted(files.items()):
        for line in text.splitlines():
            body = line[2:].strip() if line.startswith("- ") else ""
            if len(body) >= DUP_MIN_CHARS and "From: [[" not in body:
                lines.append((rel, body, fold.fold(body, preset)))
    del lines[DUP_MAX_LINES:]
    groups: list[dict[str, Any]] = []
    owner: dict[int, int] = {}
    for i, (rel, _, folded) in enumerate(lines):
        for j in range(i):
            other_rel, _, other = lines[j]
            if other_rel == rel or abs(len(other) - len(folded)) > 0.15 * len(folded):
                continue
            matcher = difflib.SequenceMatcher(None, other, folded, autojunk=False)
            if matcher.real_quick_ratio() < writegate.NEAR_RATIO:
                continue
            if matcher.quick_ratio() >= writegate.NEAR_RATIO and (
                matcher.ratio() >= writegate.NEAR_RATIO
            ):
                index = owner.get(j)
                if index is None:
                    index = len(groups)
                    groups.append({"text": lines[j][1][:100], "files": [lines[j][0]]})
                    owner[j] = index
                owner[i] = index
                if rel not in groups[index]["files"]:
                    groups[index]["files"].append(rel)
                break
    return groups


def memory(raw: Raw) -> dict[str, Any]:
    total = raw["keys"]["total"] if raw.get("keys") else 0
    decay = raw.get("decay")
    return {
        "keys": raw["keys"] if total else None,
        "decay": decay if decay and decay.get("trace_start") is not None else None,
        "inbox": raw["inbox"] if raw["inbox"]["items"] else None,
        "duplicates": raw["duplicates"],
    }


def retrieval(raw: Raw) -> dict[str, Any]:
    recall = raw.get("recall")
    paired = raw.get("paired")
    return {
        "recall": recall if recall and recall["with_evidence"] else None,
        "paired": paired if paired and paired["n"] else None,
    }


def proposal(level: int, text: str) -> dict[str, Any]:
    return {"level": level, "text": text}


def arm_proposals(arms: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    found = []
    for arm in arms:
        name = arm["name"]
        if arm["status"] == "decided" and arm["decided_in_week"]:
            found.append(proposal(2, f"arm {name} decided; pre-registered: {arm['decision']}"))
        elif arm["status"] == "running" and arm["stop_fired"]:
            found.append(
                proposal(2, f"arm {name}: the stop rule fired; run `juno arm decide {name}`")
            )
    return found


def proposals(report: Mapping[str, Any], raw: Raw) -> list[dict[str, Any]]:
    found = arm_proposals(report["arms"] or ())
    paired = report["retrieval"]["paired"]
    if paired and paired["p"] < PAIRED_P and paired["only_w"] != paired["only_0"]:
        side = "keys" if paired["only_w"] > paired["only_0"] else "no keys"
        counts = f"{paired['only_w']} vs {paired['only_0']}, p={paired['p']}"
        found.append(
            proposal(2, f"paired comparison favours {side} ({counts}); review `[keys] weight`")
        )
    c = report["signals"]["corrections"]
    before = c and c["prev_per_100"]
    if c and before and c["prompts"] >= MIN_PROMPTS and c["per_100"] >= RISE * before:
        rise = round(100 * (c["per_100"] / before - 1))
        sections = ", ".join(raw["core_sections"]) or "its open sections"
        found.append(
            proposal(
                3,
                f"corrections up {rise}% ({before:.1f} to {c['per_100']:.1f} per 100 prompts): "
                f"review core.md ({sections})?",
            )
        )
    for kind, seen in sorted((report["signals"]["sections"] or {}).items()):
        if seen["runs"] >= MIN_RUNS and seen["dropped"] * 2 > seen["runs"]:
            counts = f"{seen['dropped']} of {seen['runs']} runs"
            found.append(
                proposal(3, f"extraction kind {kind} was dropped in {counts}: review its prompt?")
            )
    return found


def paired_alt(pairs: Sequence[tuple[Mapping[str, Any], set[str]]], shown: int) -> dict[str, Any]:
    both: dict[float, list[bool]] = {0.0: [], 1.0: []}
    for entry, needed in pairs:
        alt = entry.get("alt")
        if not alt:
            continue
        both[0.0].append(bool(needed & set(alt)))
        both[1.0].append(bool(needed & {s["path"] for s in entry.get("suggestions", [])[:shown]}))
    result = keys.paired(both)["1"] if both[0.0] else {}
    return {"n": len(both[0.0]), **result}


def build(raw: Raw) -> dict[str, Any]:
    report: dict[str, Any] = {
        "week": raw["label"],
        "generated": raw["generated"],
        "signals": {
            "corrections": corrections(raw),
            "recommended_pick": recommended_pick(raw["asks"]),
            "pushback": pushback(raw["runs"]),
            "card_cost": card_cost(raw["runs"]),
            "sections": section_drops(raw["runs"]),
            "hook_errors": raw["hook_errors"],
            "writes": write_counts(raw["writes"]),
            "pitfall_repeats": raw["pitfall_repeats"],
        },
        "retrieval": retrieval(raw),
        "arms": raw["arms"] or None,
        "memory": memory(raw),
    }
    report["proposals"] = proposals(report, raw)
    return report
