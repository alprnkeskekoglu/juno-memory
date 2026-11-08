import os
import time
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any

from juno import config
from juno.adapters import (
    armsio,
    asklog,
    cardjob,
    evalio,
    fs,
    keysbackfill,
    shadowio,
    status,
    store,
    usageio,
    writelog,
)
from juno.core import decay, fold, isoweek, report, shadow, topics
from juno.core import eval as evalcore

REPORTS_DIR = "_generated/reports"
LOCK = "report.lock"


def rel_of(label: str) -> str:
    return f"{REPORTS_DIR}/{label}.md"


def path_of(cfg: config.Config, label: str) -> str:
    return os.path.join(cfg.vault, *rel_of(label).split("/"))


def exists(cfg: config.Config, label: str) -> bool:
    return os.path.isfile(path_of(cfg, label))


def inside(rows: Sequence[Mapping[str, Any]], key: str, start: float, end: float) -> list[Any]:
    return [r for r in rows if isinstance(r.get(key), int | float) and start <= r[key] < end]


def hook_errors(cfg: config.Config, start: float, end: float) -> dict[str, int] | None:
    if not os.path.exists(os.path.join(cfg.state, "hook-errors.log")):
        return None
    found: dict[str, int] = {}
    for stamp, parts in status.error_rows(cfg):
        if start <= stamp < end:
            found[parts[1]] = found.get(parts[1], 0) + 1
    return found


def pitfall_repeats(cfg: config.Config, start: float, end: float) -> list[dict[str, Any]] | None:
    directory = os.path.join(cfg.state, "topics")
    try:
        names = os.listdir(directory)
    except OSError:
        return None
    sessions: dict[str, set[str]] = {}
    for name in names:
        sid, _, rest = name.partition(".")
        marker = rest.partition(".")[2]
        try:
            stamp = os.path.getmtime(os.path.join(directory, name))
        except OSError:
            continue
        if marker.startswith("pitfall-") and start <= stamp < end:
            sessions.setdefault(marker, set()).add(sid)
    preset = cfg.get_str("language", "preset")
    text = store.read_text(os.path.join(cfg.vault, topics.PITFALLS_FILE))
    texts = {
        topics.pitfall_name(f["text"], preset): f["text"]
        for f in topics.pitfall_lines(text, preset)
    }
    repeated = [
        {"text": texts.get(name, name)[:100], "sessions": len(sids)}
        for name, sids in sessions.items()
        if len(sids) >= 2
    ]
    return sorted(repeated, key=lambda x: (-x["sessions"], x["text"]))


def write_records(cfg: config.Config, start: float, end: float) -> list[dict[str, Any]]:
    records = writelog.read(cfg)
    verbs = {str(r.get("id")): str(r.get("verb")) for r in records}
    found = []
    for rec in inside(records, "t", start, end):
        undid = verbs.get(str(rec.get("of"))) if rec.get("verb") == "undo" else None
        found.append({"verb": rec.get("verb"), "undid": undid})
    return found


def retrieval(cfg: config.Config, entries: Sequence[shadow.Entry]) -> tuple[Any, Any]:
    vault = os.path.realpath(cfg.vault)
    touches = evalio.touches_reader(evalio.transcripts_dir(cfg), vault)

    def exists_in_vault(rel: str) -> bool:
        return os.path.isfile(os.path.join(vault, rel))

    recall = evalcore.recall_from(entries, touches, exists_in_vault, 0.0)
    pairs = evalcore.evidence_entries(entries, touches, exists_in_vault)
    return recall, report.paired_alt(pairs, shadow.SHOWN_SUGGESTIONS)


def arm_view(
    cfg: config.Config, rows: Sequence[Mapping[str, Any]], now: float, start: float, end: float
) -> list[dict[str, Any]]:
    view = []
    for row in rows:
        data = armsio.read_arm(armsio.arm_path(cfg, row["name"])) or {}
        prereg: dict[str, Any] = data.get("prereg") or {}
        outcome = row.get("live") or row.get("result") or {}
        running = row.get("status") == "running"
        decided = (row.get("result") or {}).get("decided")
        view.append(
            {
                "name": row["name"],
                "status": row.get("status"),
                "n": outcome.get("n") if isinstance(outcome.get("n"), dict) else None,
                "min_n": row.get("min_n"),
                "max_days": row.get("max_days"),
                "days": int((now - row["started"]) // report.DAY)
                if running and row.get("started")
                else None,
                "stop_fired": bool(outcome.get("stop_fired")),
                "stop_rule": str(prereg.get("stop_rule", "-")),
                "decision": str(prereg.get("decision", "-")),
                "decided_in_week": isinstance(decided, int | float) and start <= decided < end,
            }
        )
    return view


def vault_lines(cfg: config.Config) -> dict[str, str]:
    files: dict[str, str] = {}
    for top in report.DUP_FILE_KINDS:
        for base, dirs, names in os.walk(os.path.join(cfg.vault, top)):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(names):
                full = os.path.join(base, name)
                if name.endswith(".md") and not os.path.islink(full):
                    files[os.path.relpath(full, cfg.vault).replace(os.sep, "/")] = store.read_text(
                        full
                    )
    return files


def keys_status(cfg: config.Config) -> dict[str, Any] | None:
    try:
        return keysbackfill.status(cfg)
    except OSError:
        return None


def collect(
    cfg: config.Config,
    label: str,
    now: float,
    arm_rows: Sequence[Mapping[str, Any]],
    decay_files: Mapping[str, float],
) -> dict[str, Any]:
    start, end = isoweek.bounds(label)
    before, _ = isoweek.bounds(isoweek.previous(label))
    entries, _ = shadowio.read(cfg.state)
    week = inside(entries, "ts", start, end)
    last = inside(entries, "ts", before, start)
    cues = cfg.language_list("correction_cues")
    preset = cfg.get_str("language", "preset")

    def cued(items: Sequence[Mapping[str, Any]]) -> int:
        return sum(fold.has_cue(str(e["prompt"]), cues, preset) for e in items)

    recall, paired = retrieval(cfg, week)
    files = vault_lines(cfg)
    return {
        "label": label,
        "generated": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
        "prompts": len(week),
        "cues": cued(week),
        "prev_prompts": len(last),
        "prev_cues": cued(last),
        "asks": inside(asklog.read(cfg.state), "t", start, end),
        "runs": inside(cardjob.read_runs(cfg), "t", start, end),
        "hook_errors": hook_errors(cfg, start, end),
        "writes": write_records(cfg, start, end),
        "pitfall_repeats": pitfall_repeats(cfg, start, end),
        "recall": recall,
        "paired": paired,
        "arms": arm_view(cfg, arm_rows, now, start, end),
        "keys": keys_status(cfg),
        "decay": decay.report(
            decay_files, usageio.read_rows(cfg.state), now, cfg.get_int("decay", "window_days")
        ),
        "inbox": report.inbox_age(store.read_text(os.path.join(cfg.vault, "inbox.md")), now),
        "duplicates": report.duplicate_groups(files, preset) if files else None,
        "core_sections": report.open_sections(store.read_text(os.path.join(cfg.vault, "core.md"))),
    }


def write(cfg: config.Config, label: str, text: str) -> str:
    target = path_of(cfg, label)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fs.atomic_write(target, text.encode("utf-8"))
    return rel_of(label)


def locked(cfg: config.Config) -> AbstractContextManager[None]:
    return fs.locked(os.path.join(cfg.state, LOCK), 0.5)
