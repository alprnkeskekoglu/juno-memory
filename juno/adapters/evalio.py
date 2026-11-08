import datetime
import hashlib
import json
import os
import time
from collections.abc import Collection, Sequence
from typing import Any

from juno import config, paths
from juno.adapters import fs, index, indexquery, ledgerio, shadowio
from juno.core import eval as core
from juno.core import fold, isoweek, shadow
from juno.core import keys as keycore

LAST_GATE = "last-gate.json"


class Sample(list[core.Entry]):
    path: str = ""


def set_dir(state: str) -> str:
    path = os.path.join(state, "eval")
    fs.ensure_private_dir(path)
    return path


def write_new(path: str, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
    finally:
        os.close(fd)


def sample_for_labelling(
    state: str,
    n: int = 30,
    seed: int = 0,
    exclude_ids: Sequence[str] = (),
    k: int | None = None,
    since: float | None = None,
) -> Sample:
    entries, _ = shadowio.read(state)
    pool = core.sample_pool(entries, set(exclude_ids), since)
    if not pool:
        raise ValueError("no shadow entries available to sample")
    out = Sample(core.pick_sample(pool, n, seed))
    payload = json.dumps(
        {"v": 1, "seed": seed, "entries": list(out)}, ensure_ascii=False, indent=1
    ).encode("utf-8")
    directory = set_dir(state)
    index = k if k is not None else 1
    while True:
        path = os.path.join(directory, f"set-{index}.json")
        try:
            write_new(path, payload)
            break
        except FileExistsError:
            if k is not None:
                raise
            index += 1
    out.path = path
    return out


def sidecar(set_path: str) -> str:
    return set_path + ".sha256"


def digest(set_path: str) -> str:
    with open(set_path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def load_raw(set_path: str) -> dict[str, Any]:
    with open(set_path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ValueError("malformed set file")
    return data


def freeze_set(set_path: str) -> str:
    core.check_labels(load_raw(set_path)["entries"])
    write_new(
        sidecar(set_path),
        (digest(set_path) + "  " + os.path.basename(set_path) + "\n").encode(),
    )
    return digest(set_path)


def load_frozen(set_path: str) -> list[core.Entry]:
    try:
        with open(sidecar(set_path), encoding="utf-8") as handle:
            want = handle.read().split()[0]
    except FileNotFoundError, IndexError:
        raise core.SetTamperedError(
            f"{set_path}: missing or empty sha256 sidecar (freeze the set first)"
        ) from None
    if digest(set_path) != want:
        raise core.SetTamperedError(f"{set_path}: content does not match its sha256 sidecar")
    entries: list[core.Entry] = load_raw(set_path)["entries"]
    core.check_labels(entries)
    return entries


def timed_runs(entries: Sequence[core.Entry], db: str, preset: str) -> list[core.Run]:
    runs: list[core.Run] = []
    for e in entries:
        started = time.perf_counter()
        indexquery.search(db, e["prompt"], limit=core.SEARCH_LIMIT, preset=preset)
        ms = (time.perf_counter() - started) * 1000
        runs.append((e, core.logged_results(e), ms))
    return runs


def retired_of(db: str) -> set[str]:
    return set(index.retired_paths(db))


def evaluate(
    set_path: str, db: str, floor: float, preset: str = "none"
) -> tuple[dict[str, Any], str]:
    runs = timed_runs(load_frozen(set_path), db, preset)
    m = core.evaluate_runs(runs, floor, retired_of(db))
    return m, core.report(m)


def choose_floor(set_path: str, db: str, preset: str = "none") -> float:
    runs = timed_runs(load_frozen(set_path), db, preset)
    return core.best_floor(runs, retired_of(db))


def gate(set_path: str, db: str, floor: float, preset: str = "none") -> dict[str, Any]:
    m, text = evaluate(set_path, db, floor, preset)
    reasons = core.gate_reasons(m)
    return {"passed": not reasons, "reasons": reasons, "metrics": m, "report": text}


def agreement(set_path: str) -> dict[str, Any]:
    return core.agreement(load_raw(set_path)["entries"])


def write_last_gate(state: str, result: dict[str, Any], set_path: str, floor: float) -> None:
    record = {
        "passed": bool(result.get("passed")),
        "reasons": list(result.get("reasons") or []),
        "metrics": result.get("metrics"),
        "set": os.path.basename(str(set_path)),
        "floor": floor,
        "t": time.time(),
    }
    text = json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, default=str)
    fs.atomic_write(os.path.join(set_dir(state), LAST_GATE), (text + "\n").encode(), 0o600)


def find_transcript(transcripts: str, segment: str) -> str | None:
    name = os.path.basename(str(segment)) + ".jsonl"
    try:
        projects = sorted(os.listdir(transcripts))
    except OSError:
        return None
    for project in projects:
        path = os.path.join(transcripts, project, name)
        if os.path.isfile(path):
            return path
    return None


def vault_rel(path: object, vault: str) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    real = os.path.realpath(os.path.expanduser(path))
    return os.path.relpath(real, vault) if real.startswith(vault + os.sep) else None


def vault_touches(path: str, vault: str) -> list[core.Touch]:
    out: list[core.Touch] = []
    with open(path, "rb") as handle:
        for raw in handle:
            if b'"tool_use"' not in raw:
                continue
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(item, dict) or item.get("type") != "assistant":
                continue
            if item.get("isSidechain"):
                continue
            ts = core.iso_ts(item.get("timestamp"))
            content = (item.get("message") or {}).get("content")
            if ts is None or not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "tool_use":
                    continue
                tool = part.get("name")
                if tool not in core.RECALL_READ_TOOLS + core.RECALL_WRITE_TOOLS:
                    continue
                given = part.get("input") or {}
                rel = vault_rel(given.get("file_path") or given.get("notebook_path"), vault)
                if rel:
                    out.append((ts, str(tool), rel))
    return out


def touches_reader(transcripts: str, vault: str) -> core.Touches:
    def touches_for(segment: str) -> list[core.Touch] | None:
        path = find_transcript(transcripts, segment)
        return vault_touches(path, vault) if path else None

    return touches_for


def ledger_sid_data(
    cfg: config.Config, sid: str
) -> tuple[list[tuple[str, float, list[str]]], dict[str, str]]:
    packs: list[tuple[str, float, list[str]]] = []
    arms: dict[str, str] = {}
    path = ledgerio.ledger_path(cfg, sid)
    raw = fs.read_bytes(path) if path else None
    for line in (raw or b"").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("ev") == "start" and isinstance(rec.get("arms"), dict):
            arms = {str(k): str(v) for k, v in rec["arms"].items()}
        elif rec.get("ev") == "pack" and isinstance(rec.get("nb"), list):
            nb = [p for p in rec["nb"] if isinstance(p, str)]
            packs.append((sid, float(rec.get("t") or 0), nb))
    return packs, arms


def ledger_next_events(cfg: config.Config, sid: str) -> list[tuple[float, str]]:
    path = ledgerio.ledger_path(cfg, sid)
    found: list[tuple[float, str]] = []
    for line in ((fs.read_bytes(path) if path else None) or b"").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("ev") != "pack":
            continue
        seg = rec.get("next_shown") or rec.get("next_held")
        if isinstance(seg, str):
            found.append((float(rec.get("t") or 0), seg))
    return found


def early_lookups(path: str, vault: str, since: float, limit: int = 10) -> int:
    calls: list[dict[str, Any]] = []
    with open(path, "rb") as handle:
        for raw in handle:
            if b'"tool_use"' not in raw:
                continue
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(item, dict) or item.get("type") != "assistant":
                continue
            ts = core.iso_ts(item.get("timestamp"))
            content = (item.get("message") or {}).get("content")
            if item.get("isSidechain") or ts is None or ts < since or not isinstance(content, list):
                continue
            calls += [p for p in content if isinstance(p, dict) and p.get("type") == "tool_use"]
    found = 0
    for part in calls[:limit]:
        given = part.get("input") or {}
        if part.get("name") == "Bash":
            found += "juno search" in str(given.get("command") or "")
        elif part.get("name") in core.RECALL_READ_TOOLS:
            found += vault_rel(given.get("file_path"), vault) is not None
    return found


def ledger_arm_data(
    cfg: config.Config, skip: Collection[str] = ()
) -> tuple[list[tuple[str, float, list[str]]], dict[str, dict[str, str]]]:
    packs: list[tuple[str, float, list[str]]] = []
    arms: dict[str, dict[str, str]] = {}
    for sid in ledgerio.ledger_ids(cfg):
        if sid in skip:
            continue
        found, sides = ledger_sid_data(cfg, sid)
        packs += found
        arms[sid] = sides
    return packs, arms


def transcripts_dir(cfg: config.Config) -> str:
    projects = cfg.env.get("JUNO_CLAUDE_PROJECTS") or os.path.join(cfg.claude_dir, "projects")
    return paths.expand(projects, cfg.env)


def read_touches(cfg: config.Config, sid: str) -> list[tuple[float, str]] | None:
    touches = touches_reader(transcripts_dir(cfg), os.path.realpath(cfg.vault))(sid)
    if touches is None:
        return None
    return [(ts, rel) for ts, tool, rel in touches if tool in core.RECALL_READ_TOOLS]


def recall_proxy(
    state: str, vault_dir: str, transcripts: str, floor: float = 0.0
) -> dict[str, Any]:
    entries, _ = shadowio.read(state)
    vault = os.path.realpath(vault_dir)
    return core.recall_from(
        entries,
        touches_reader(transcripts, vault),
        lambda rel: os.path.isfile(os.path.join(vault, rel)),
        floor,
    )


def recall_weekly(
    state: str, vault_dir: str, transcripts: str, now: float, weeks: int
) -> list[dict[str, Any]]:
    """recall_proxy per ISO week; a segment counts in the week of its first prompt."""
    entries, _ = shadowio.read(state)
    vault = os.path.realpath(vault_dir)
    touches = touches_reader(transcripts, vault)
    first: dict[str, float] = {}
    for e in entries:
        seg = str(e["segment"])
        first[seg] = min(first.get(seg, e["ts"]), e["ts"])
    today = datetime.date.fromtimestamp(now)
    labels = [isoweek.week_label(today - datetime.timedelta(days=7 * i)) for i in range(weeks)]
    out: list[dict[str, Any]] = []
    for label in reversed(labels):
        start, end = isoweek.bounds(label)
        week = [e for e in entries if start <= first[str(e["segment"])] < end]
        m = core.recall_from(
            week, touches, lambda rel: os.path.isfile(os.path.join(vault, rel)), 0.0
        )
        out.append(
            {
                "week": label,
                "start": time.strftime("%Y-%m-%d", time.localtime(start)),
                "n": m["with_evidence"],
                "hits": m["hits"],
                "pack_hits": m["pack_hits"],
                "recall": m["recall"],
                "pack_recall": m["pack_recall"],
                "wilson_lower": m["wilson_lower"],
                "pack_wilson_lower": m["pack_wilson_lower"],
            }
        )
    return out


def card_ab(
    state: str,
    vault_dir: str,
    transcripts: str,
    cues: Sequence[str] | None = None,
    preset: str | None = None,
) -> dict[str, Any]:
    if cues is None or preset is None:
        cfg = config.load()
        preset = cfg.get_str("language", "preset")
        cues = cfg.language_list("correction_cues")
    entries, _ = shadowio.read(state)
    vault = os.path.realpath(vault_dir)
    used = preset
    wanted = list(cues)
    return core.card_ab_from(
        entries,
        touches_reader(transcripts, vault),
        lambda text: fold.has_cue(text, wanted, used),
    )


def compare_keys(
    cfg: config.Config, transcripts: str, db: str, weights: Sequence[float]
) -> dict[str, Any]:
    state, vault = cfg.state, os.path.realpath(cfg.vault)
    entries, _ = shadowio.read(state)
    pairs = core.evidence_entries(
        entries,
        touches_reader(transcripts, vault),
        lambda rel: os.path.isfile(os.path.join(vault, rel)),
    )
    preset = cfg.get_str("language", "preset")
    tickets = bool(cfg.get_list("jira", "prefixes"))
    hits: dict[float, list[bool]] = {}
    for weight in sorted({0.0, *weights}):
        got = []
        for e, needed in pairs:
            found = indexquery.search(
                db,
                e["prompt"],
                limit=shadowio.OVERFETCH,
                preset=preset,
                ticket_files=tickets,
                keys_weight=weight,
            )
            paths = [h["relpath"] for h in found if shadow.suggestable(h["relpath"])]
            got.append(bool(needed & set(paths[: shadow.SHOWN_SUGGESTIONS])))
        hits[weight] = got
    return {
        "setting": "keys",
        "n": len(pairs),
        "hits": {f"{w:g}": sum(got) for w, got in sorted(hits.items())},
        "weights": keycore.paired(hits),
    }
