import os
import time
from typing import Any

from juno import config, hookcore
from juno.adapters import (
    armsio,
    cardjob,
    ledgerio,
    prune,
    reportauto,
    repos,
    selfio,
    stalerun,
    status,
    store,
    stubs,
    topicsio,
)
from juno.adapters.vaultview import DiskVault
from juno.core import inject, packs, routing
from juno.core.packs import Caps
from juno.core.tomlio import read_key, set_runtime
from juno.handlers import delivery
from juno.handlers.common import capture_title, payload_cwd


def rules_text(cfg: config.Config, view: DiskVault) -> str:
    return inject.rules_block(
        view.read("core.md"),
        "\n".join(
            "- " + inject.neutralize(r) for r in packs.visible_rules(view, "rules/global.md")
        ),
        cfg.get_int("caps", "core_chars"),
        cfg.get_int("caps", "global_rules_chars"),
        cfg.get_int("caps", "hook_output_chars"),
    )


def refresh_plugin_root(cfg: config.Config) -> None:
    root = cfg.env.get("CLAUDE_PLUGIN_ROOT", "")
    python = cfg.get_str("runtime", "python")
    if not root or not python or not os.path.isfile(cfg.path):
        return
    if cfg.get_str("runtime", "plugin_root") == root:
        return
    with open(cfg.path, encoding="utf-8") as handle:
        text = handle.read()
    if read_key(text, "python") is None:
        return
    updated = set_runtime(text, python, root)
    if updated != text:
        from juno.adapters import fs

        fs.atomic_write(cfg.path, updated.encode("utf-8"))


def segment_line(sid: str) -> str:
    stamp = routing.local_stamp()
    return (
        f"Juno segment id: {sid[:8]}. This segment's handoff card heading ends with `· {sid[:8]}`. "
        f"Current local time is {stamp}; use heading time {stamp}, never a guessed time."
    )


def hook_error_line(cfg: config.Config) -> str:
    repeated = status.repeated_errors(cfg)
    if not repeated:
        return ""
    (event, kind, where), count = repeated[0]
    others = len(repeated) - 1
    hours = cfg.get_int("thresholds", "hook_error_window_hours")
    extra = f" (+{others} other repeated)" if others else ""
    line = (
        f"Juno: hook error {event} {kind} at {where} x{count} in {hours} h{extra}; see juno doctor"
    )
    return inject.trim(inject.neutralize(line), 300, "hook-errors.log")


def weekly_report_line(cfg: config.Config) -> str:
    try:
        return reportauto.tick(cfg)
    except OSError as exc:
        store.log_error(cfg, "weekly-report", exc)
        return ""


def stalefix_line(cfg: config.Config) -> str:
    try:
        return stalerun.notice(cfg)
    except OSError as exc:
        store.log_error(cfg, "stale-fixer", exc)
        return ""


def extra_blocks(cfg: config.Config, source: str) -> list[str]:
    if source == "compact":
        return []
    from juno.adapters import learned, queues
    from juno.core import candidates

    if learned.stale(cfg):
        learned.rebuild(cfg)
    nudges = [
        candidates.rules_nudge(queues.pending(cfg, queues.RULES)),
        candidates.threads_nudge(queues.pending(cfg, queues.THREADS)),
    ]
    return [inject.context_block([n]) for n in nudges if n]


def start_context(
    cfg: config.Config,
    view: DiskVault,
    seg: Any,
    found: dict[str, str | None],
    cwd: str,
    budget: int,
) -> str:
    if found:
        return delivery.deliver_packs(cfg, view, seg, packs.wanted_for(found), budget)
    if repos.is_overview_dir(cfg, cwd):
        cards = packs.all_cards(view)
        text = packs.overview(
            view, Caps.from_config(cfg), cards, time.time(), repos.tickets_from(cfg).enabled
        )
        return inject.trim(text, min(budget, cfg.get_int("caps", "overview_chars")), "the vault")
    return ""


def session_context(
    payload: dict[str, Any], cfg: config.Config, view: DiskVault, budget: int
) -> str:
    sid = str(payload.get("session_id") or "")
    cwd = payload_cwd(payload)
    source = str(payload.get("source") or "startup")[:20]
    prune.prune_state(cfg)
    refresh_plugin_root(cfg)
    seg = ledgerio.fold(cfg, sid)
    found = repos.repos_for(cfg, [cwd])
    start: dict[str, Any] = {
        "ev": "start",
        "source": source,
        "cwd": cwd,
        "repos": [[repo, ticket] for repo, ticket in found.items()],
    }
    transcript = payload.get("transcript_path")
    if isinstance(transcript, str) and transcript:
        start["transcript"] = transcript[:500]
    arms = armsio.arms_for(cfg, sid)
    if arms:
        start["arms"] = arms
    ledgerio.record(cfg, seg, start)
    capture_title(cfg, seg, payload)
    compact = source == "compact"
    if compact:
        ledgerio.record(cfg, seg, {"ev": "packs_reset"})
        topicsio.reset(cfg, sid)
    previous = None if compact else stubs.stub_ended_segments(cfg, sid)
    lines = [segment_line(sid), f"Juno vault: {cfg.vault}."]
    if previous:
        lines.append(routing.previous_segment_line(
                *previous, failure=cardjob.card_failure(cfg, previous[0].sid)
            ))
    if not compact:
        lines.append(hook_error_line(cfg))
        lines.append(weekly_report_line(cfg))
        lines.append(stalefix_line(cfg))
    blocks = [inject.context_block(lines)]
    if not compact:
        blocks.append(topicsio.general_block(cfg))
    blocks += extra_blocks(cfg, source)
    if arms.get("self") != "off":
        blocks.append(selfio.model_block(cfg))
    left = budget - sum(len(b) + 2 for b in blocks)
    blocks.append(start_context(cfg, view, seg, found, cwd, left))
    status.update(cfg, seg)
    return "\n\n".join(b for b in blocks if b)


def handle(payload: dict[str, Any], cfg: config.Config) -> dict[str, Any] | None:
    if not os.path.isdir(cfg.vault):
        return None
    view = DiskVault(cfg)
    cap = cfg.get_int("caps", "hook_output_chars")
    rules = rules_text(cfg, view)
    context = session_context(payload, cfg, view, cap - len(rules) - 2)
    text = inject.fit_blocks([rules, context], cap)
    return hookcore.context_output("SessionStart", text) if text else None
