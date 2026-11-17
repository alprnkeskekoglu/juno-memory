"""Writing-voice import, forget and status (12.13): the state-file side of `juno voice`."""

import contextlib
import json
import os
from typing import Any

from juno import config
from juno.adapters import fs, ledgerio, voiceio
from juno.core import voiceimport, voicemeasure

PREFIX = "import:"
DOCTOR_FACTOR = 10
Row = voiceio.Row


def counted(rows: list[Row], reg: str) -> int:
    return sum(
        1
        for r in rows
        if r["k"] == "sample" and r.get("reg") == reg and r.get("by") in voicemeasure.COUNTED
    )


def register_report(
    rows: list[Row], reg: str, minimum: int, plan: voiceimport.Plan
) -> dict[str, Any]:
    skipped = plan.skipped[reg]
    n = counted(rows, reg)
    return {
        "accepted": plan.accepted[reg],
        "skipped": dict(skipped),
        "skipped_total": sum(skipped.values()),
        "counted": n,
        "ready": n >= minimum,
    }


def remember_names(cfg: config.Config, texts: list[str]) -> None:
    found = voiceio.read_names(cfg)
    for text in texts:
        found |= voicemeasure.name_tokens(text)
    if found == voiceio.read_names(cfg):
        return
    fs.ensure_private_dir(cfg.state)
    kept = sorted(found)[: voiceio.MAX_NAMES]
    fs.atomic_write(voiceio.names_path(cfg), json.dumps(kept, ensure_ascii=False).encode(), 0o600)


def run_import(
    cfg: config.Config, raw: str, register: str | None, target: str | None, dry_run: bool
) -> dict[str, Any]:
    """Filter, store and report. The caller holds the vault lock for a real run. The report is
    built from the rows a real run would leave, so a dry run prints the same numbers."""
    rows = voiceio.read_rows(cfg)
    plan = voiceimport.plan(
        voiceimport.parse(raw, register, target),
        {str(r.get("text")) for r in rows if r["k"] == "sample"},
        cfg.privacy_prefixes(),
        cfg.get_int("voice", "import_max"),
        cfg.get_int("voice", "import_max_age_days"),
    )
    merged = voiceio.pruned(rows + plan.rows, cfg.get_int("voice", "import_max"))
    minimum = cfg.get_int("voice", "min_samples")
    report: dict[str, Any] = {
        "batch": plan.batch,
        "dry_run": dry_run,
        "accepted": sum(plan.accepted.values()),
        "skipped": sum(sum(s.values()) for s in plan.skipped.values()),
        "registers": {
            reg: register_report(merged, reg, minimum, plan) for reg in voiceimport.REGISTERS
        },
    }
    ready = any(r["ready"] for r in report["registers"].values())
    report["profile"] = bool(plan.rows) and ready
    if dry_run or not plan.rows:
        return report
    voiceio.write_rows(cfg, merged)
    remember_names(cfg, [str(r["text"]) for r in plan.rows])
    report["profile"] = voiceio.write_profile(cfg, merged, held=True)
    return report


def run_forget(cfg: config.Config, batch: str) -> dict[str, Any]:
    """Remove the rows of one batch and rebuild `self/voice.md`, or remove it when no register
    is ready any more. The caller holds the vault lock."""
    rows = voiceio.read_rows(cfg)
    kept = [r for r in rows if r.get("sid") != PREFIX + batch]
    removed = len(rows) - len(kept)
    profile = False
    if removed:
        voiceio.write_rows(cfg, kept)
        profile = voiceio.write_profile(cfg, kept, held=True)
        if not profile:
            with contextlib.suppress(FileNotFoundError):
                os.remove(os.path.join(cfg.vault, "self", "voice.md"))
    return {"batch": batch, "removed": removed, "profile": profile}


def status(cfg: config.Config) -> dict[str, Any]:
    rows = voiceio.read_rows(cfg)
    minimum = cfg.get_int("voice", "min_samples")
    regs: dict[str, Any] = {}
    for reg in voiceimport.REGISTERS:
        by = {
            kind: sum(
                1
                for r in rows
                if r["k"] == "sample" and r.get("reg") == reg and r.get("by") == kind
            )
            for kind in voicemeasure.COUNTED
        }
        total = sum(by.values())
        regs[reg] = {**by, "counted": total, "min_samples": minimum, "ready": total >= minimum}
    return {"enabled": voiceio.enabled(cfg), "min_samples": minimum, "registers": regs}


def user_turns(cfg: config.Config, stop_at: int) -> int:
    """User turns across all session ledgers, counted from the first part of each prompt record;
    stops as soon as `stop_at` is reached. Synthetic prompts are not told apart (cheap)."""
    total = 0
    for sid in ledgerio.ledger_ids(cfg):
        path = ledgerio.ledger_path(cfg, sid)
        data = fs.read_bytes(path) if path else None
        for line in (data or b"").splitlines():
            if b'"ev":"prompt"' in line and b'"part":0,' in line:
                total += 1
        if total >= stop_at:
            break
    return total


def doctor_line(cfg: config.Config) -> list[tuple[str, str, str]]:
    """Suggest `juno voice import` when the voice is enabled, still has no profile and the
    user has already typed plenty."""
    if not voiceio.enabled(cfg):
        return []
    state = status(cfg)
    if any(r["ready"] for r in state["registers"].values()):
        return []
    need = cfg.get_int("thresholds", "meaningful_user_turns") * DOCTOR_FACTOR
    if user_turns(cfg, need) < need:
        return []
    return [
        (
            "INFO",
            "voice",
            f"no register has {state['min_samples']} counted samples after {need} user turns; "
            "`juno voice import` adds your own past messages",
        )
    ]
