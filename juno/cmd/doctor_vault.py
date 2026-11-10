import json
import os
import sqlite3
import time
from typing import Any

from juno import config
from juno.adapters import cardjob, learned, ledgerio, protocolio, queues, repos, store
from juno.adapters import status as status_io
from juno.adapters.vaultview import DiskVault, scan_metas
from juno.cmd import backlog as backlog_cmd
from juno.core import backlog as bl
from juno.core import doctor_vault as dv
from juno.core import retire, topics
from juno.core.doctor_vault import Check
from juno.core.vaultfmt import (
    NEVER_INJECTED_KINDS,
    kind_of,
    parse_cards,
    parse_frontmatter,
)
from juno.core.vaultfmt import field_items as fm_items

PROBE = {
    "session_id": "doctor-probe",
    "cwd": "/",
    "hook_event_name": "PreToolUse",
    "tool_name": "Edit",
    "tool_input": {"file_path": "/tmp/juno-doctor-probe.txt", "old_string": "x", "new_string": "y"},
}
PROBE_RUNS = 5
BACKLOG_STALE_DAYS = 60


def markdown_files(vault: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for rel in scan_metas(vault):
        found[rel] = store.read_text(os.path.join(vault, *rel.split("/")))
    return found


def all_cards(view: DiskVault) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for name in view.names("handoffs"):
        rel = f"handoffs/{name}"
        text = view.read(rel)
        if not view.hidden(rel, text):
            cards.extend(parse_cards(text, rel))
    return cards


def learned_stale(cfg: config.Config) -> bool | None:
    if learned.handoff_files(cfg) and not learned.built(cfg):
        return None
    return learned.stale(cfg)


def rule_names(view: DiskVault) -> list[str]:
    names = view.names("rules")
    for sub in (topics.REPO_DIR, topics.TOPIC_DIR):
        names += [f"{sub}/{n}" for n in view.names(f"rules/{sub}")]
    return names


def vault_checks(cfg: config.Config) -> list[Check]:
    view = DiskVault(cfg)
    cards = all_cards(view)
    valid = [c for c in cards if c["valid"]]
    out = dv.card_checks(cards)
    rule_files = {n: view.read(f"rules/{n}") for n in rule_names(view)}
    out += dv.rule_checks(
        rule_files,
        cfg.get_int("caps", "global_rule_lines"),
        cfg.get_int("caps", "scoped_rule_lines"),
        cfg.get_str("language", "preset"),
    )
    out += dv.budget_checks(
        view.read("core.md"),
        view.read("rules/global.md"),
        cfg.get_int("caps", "core_chars"),
        cfg.get_int("caps", "global_rules_chars"),
    )
    card_repos = [r for c in valid for r in c.get("repos") or [] if not repos.plain_folder(cfg, r)]
    profiles = {n[:-3] for n in view.names("projects")}
    out += dv.profile_checks(card_repos, profiles)
    out += index_checks(cfg)
    resolution = retire.resolve(scan_metas(cfg.vault))
    out += dv.retire_checks(resolution.pairs, resolution.dead)
    sizes = {n: len(parse_cards(view.read(f"handoffs/{n}"), n)) for n in view.names("handoffs")}
    out += dv.handoff_size_checks(sizes)
    out += dv.learned_check(learned_stale(cfg))
    out += content_checks(cfg, view)
    return out


def index_checks(cfg: config.Config) -> list[Check]:
    db = os.path.join(cfg.state, "index.sqlite3")
    if not os.path.exists(db):
        return [("INFO", "index", "no search index yet (built by juno search)")]
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
        try:
            found = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as error:
        return [
            (
                "WARN",
                "index",
                f"search index unreadable ({type(error).__name__}); juno search rebuilds it",
            )
        ]
    if found and found[0] == "ok":
        return [("PASS", "index", "search index readable")]
    return [("WARN", "index", "search index failed its integrity check; juno search rebuilds it")]


def content_checks(cfg: config.Config, view: DiskVault) -> list[Check]:
    files = {
        rel: text
        for rel, text in markdown_files(cfg.vault).items()
        if kind_of(rel) not in NEVER_INJECTED_KINDS and kind_of(rel) != "archive"
    }
    out = dv.privacy_checks(files, cfg.privacy_prefixes())
    out += dv.link_checks(files)
    out += dv.note_checks({n: view.read(f"notes/{n}") for n in view.names("notes")})
    knowledge: dict[str, tuple[str, bool]] = {}
    for name in view.names("knowledge"):
        text = view.read(f"knowledge/{name}")
        knowledge[name] = (text, bool(fm_items(parse_frontmatter(text)[0], "sources")))
    out += dv.knowledge_checks(knowledge)
    out += dv.pitfall_checks(view.read("pitfalls.md"), cfg.get_str("language", "preset"))
    pending_threads = len(queues.pending(cfg, queues.THREADS))
    out += dv.thread_checks(
        view.read("threads.md"),
        cfg.get_int("thresholds", "thread_stale_days"),
        pending_threads,
        time.time(),
    )
    out.append(dv.thread_summary(view.read("threads.md"), time.time()))
    return out


def protocol_checks(cfg: config.Config) -> list[Check]:
    out: list[Check] = []
    for name, state in protocolio.scan(cfg):
        if state == "outdated":
            out.append(
                (
                    "WARN",
                    "protocol",
                    f"protocol/{name} is an older shipped version; "
                    "run juno migrate --protocol --dry-run, then juno migrate --protocol",
                )
            )
        elif state == "missing":
            out.append(("WARN", "protocol", f"protocol/{name} is missing; juno migrate --protocol"))
        elif state == "modified":
            out.append(
                (
                    "INFO",
                    "protocol",
                    f"protocol/{name} was edited here and differs from the template; left alone",
                )
            )
    return out or [("PASS", "protocol", "protocol files match the plugin's templates")]


def backlog_checks(cfg: config.Config) -> list[Check]:
    rows = backlog_cmd.load_items(cfg)
    pending = len(queues.pending(cfg, queues.BACKLOG))
    out = dv.backlog_checks(rows, time.strftime("%Y-%m-%d"), pending, bl.CLOSED)
    live = [r for r in rows if not r["problem"] and r["status"] not in bl.CLOSED]
    return out + dv.stale_item_checks(live, time.time(), BACKLOG_STALE_DAYS)


def stale_cache_checks(cfg: config.Config) -> list[Check]:
    view = DiskVault(cfg)
    data = view.stale_cache()
    files = data.get("files")
    if not isinstance(files, dict):
        return dv.stale_cache_checks(None, None)
    generated = data.get("generated")
    stamp = (
        time.strftime("%Y-%m-%d", time.localtime(generated)) if isinstance(generated, int) else "-"
    )
    return dv.stale_cache_checks([r for r in files if view.stale_since(r)], stamp)


def runs_checks(cfg: config.Config) -> list[Check]:
    rows = cardjob.read_runs(cfg)
    now = time.time()
    pending = {
        "rules": len(queues.pending(cfg, queues.RULES)),
        "threads": len(queues.pending(cfg, queues.THREADS)),
        "backlog": len(queues.pending(cfg, queues.BACKLOG)),
    }
    out = dv.card_run_checks(rows, cfg.get_str("handoff", "mode"), now)
    return out + dv.learning_checks(rows, pending, now)


def shadow_checks(cfg: config.Config) -> list[Check]:
    path = os.path.join(cfg.state, "shadow.jsonl")
    enabled = cfg.get_bool("shadow", "enabled")
    if not os.path.exists(path):
        return dv.shadow_checks(enabled, None, 0)
    stamps: list[float] = []
    bad = 0
    for line in store.read_text(path).splitlines():
        try:
            row = json.loads(line)
            stamp = row.get("ts", row.get("t"))
        except ValueError, AttributeError:
            bad += 1
            continue
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
            stamps.append(float(stamp))
        else:
            bad += 1
    return dv.shadow_checks(enabled, stamps, bad)


def timing_lines(cfg: config.Config) -> list[str]:
    lines: list[str] = []
    for name in ("hook-timings.log.1", "hook-timings.log"):
        lines += store.read_text(os.path.join(cfg.state, name)).splitlines()
    return lines


def error_checks(cfg: config.Config) -> list[Check]:
    count, last = status_io.error_summary(cfg)
    return dv.error_checks(count, last, len(status_io.repeated_errors(cfg)))


def probe(cfg: config.Config) -> Check:
    from juno.handlers import pre_tool_use

    times: list[float] = []
    for _ in range(PROBE_RUNS):
        start = time.monotonic()
        pre_tool_use.handle(dict(PROBE), cfg)
        times.append((time.monotonic() - start) * 1000)
    return dv.probe_check(times)


def collect(cfg: config.Config, run_probe: bool) -> list[Check]:
    out = error_checks(cfg) + dv.timing_checks(timing_lines(cfg))
    out.append(probe(cfg) if run_probe else ("INFO", "pretooluse", "probe skipped (read-only)"))
    out += vault_checks(cfg)
    out += protocol_checks(cfg)
    out += stale_cache_checks(cfg)
    out += backlog_checks(cfg)
    out += runs_checks(cfg)
    out += shadow_checks(cfg)
    out += dv.synthetic_checks(ledgerio.synthetic_report(cfg))
    return out
