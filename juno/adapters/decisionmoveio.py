"""Read the vault for the decision move, and apply its plan (#31)."""

import json
import os
import time
from collections.abc import Callable

from juno import config
from juno.adapters import fs, privacyack, repos, store
from juno.adapters.vaultview import SKIP_DIRS, STALE_FILE, DiskVault
from juno.core import decisionmove, owners, vaultfmt
from juno.core import privacyack as ack_core


def vault_path(cfg: config.Config, rel: str) -> str:
    return os.path.join(cfg.vault, *rel.split("/"))


def plan_for(cfg: config.Config) -> decisionmove.Plan:
    view = DiskVault(cfg)
    names = view.names("decisions")
    rels = {f"decisions/{n}": view.read(f"decisions/{n}") for n in names}
    profiles = [n[:-3] for n in view.names("projects") if not view.hidden(f"projects/{n}")]
    tickets = repos.tickets_from(cfg)
    hidden: dict[str, str] = {}
    owner_texts: dict[str, str] = {}
    archive_texts: dict[str, str] = {}
    for rel, text in rels.items():
        found = decisionmove.owner_of(rel, profiles, tickets)
        reason = vaultfmt.hidden_reason(rel, text, view.retired)
        if reason:
            hidden[rel] = reason
        if found is None:
            continue
        if view.exists(found.rel):
            owner_texts[found.rel] = view.read(found.rel)
            why = vaultfmt.hidden_reason(found.rel, owner_texts[found.rel], view.retired)
            if why:
                hidden[found.rel] = why
        archive = owners.archive_rel(found.topic)
        if view.exists(archive):
            archive_texts[archive] = view.read(archive)
    return decisionmove.plan(
        rels,
        profiles,
        tickets,
        owner_texts,
        archive_texts,
        hidden,
        time.strftime("%Y-%m-%d"),
        cfg.get_str("jira", "base_url"),
    )


def vault_texts(cfg: config.Config) -> dict[str, str]:
    found: dict[str, str] = {}
    for base, dirs, files in os.walk(cfg.vault):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if name.endswith(".md") and not name.startswith("."):
                path = os.path.join(base, name)
                found[os.path.relpath(path, cfg.vault).replace(os.sep, "/")] = store.read_text(path)
    return found


def count_links(cfg: config.Config, plan: decisionmove.Plan) -> int:
    return decisionmove.count_path_links(
        vault_texts(cfg),
        {m.source for m in plan.moves} | {d.source for d in plan.drops},
    )


def write_checked(cfg: config.Config, rel: str, text: str, mtime: float | None) -> bool:
    path = vault_path(cfg, rel)
    store.backup(cfg, path)
    fs.ensure_private_dir(os.path.dirname(path))
    store.write_text(path, text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return fs.read_bytes(path) == text.encode("utf-8")


def resign_stale(cfg: config.Config, rel: str) -> None:
    """Give the file's stale entry the file's new signature, so its mark survives a rewrite."""
    path = os.path.join(cfg.state, STALE_FILE)
    data = store.read_json_dict(path)
    files = data.get("files")
    entry = files.get(rel) if isinstance(files, dict) else None
    sig = store.file_sig(vault_path(cfg, rel))
    if isinstance(entry, dict) and sig is not None:
        entry["sig"] = sig
        text = json.dumps(data, ensure_ascii=False, sort_keys=True)
        fs.atomic_write(path, (text + "\n").encode("utf-8"), 0o600)


def apply(cfg: config.Config, plan: decisionmove.Plan, say: Callable[[str], None]) -> int:
    """Archive, then owner, read both back, then remove the source; stop at the first mismatch."""
    for move in plan.moves:
        source = vault_path(cfg, move.source)
        if not os.path.exists(source):
            continue  # a case-insensitive filesystem: an earlier move already took this file
        owner = vault_path(cfg, move.owner.rel)
        if os.path.exists(owner) and os.path.samefile(source, owner):
            say(f"decisions: {move.source} and {move.owner.rel} are one file; left alone")
            return 1
        # a stub takes the decision file's mtime, an existing owner keeps its own
        mtime = os.path.getmtime(owner) if os.path.exists(owner) else os.path.getmtime(source)
        if move.archive_text is not None and not write_checked(
            cfg, move.archive_rel, move.archive_text, None
        ):
            say(f"decisions: {move.archive_rel} did not read back as written; {move.source} kept")
            return 1
        marked = DiskVault(cfg).stale_since(move.owner.rel) is not None
        if not write_checked(cfg, move.owner.rel, move.owner_text, mtime):
            say(f"decisions: {move.owner.rel} did not read back as written; {move.source} kept")
            return 1
        if marked:
            resign_stale(cfg, move.owner.rel)
        store.backup(cfg, source)
        os.remove(source)
    for drop in plan.drops:
        source = vault_path(cfg, drop.source)
        if not os.path.exists(source):
            continue
        if drop.archive_text is not None and not write_checked(
            cfg, drop.archive_rel, drop.archive_text, None
        ):
            say(f"decisions: {drop.archive_rel} did not read back as written; {drop.source} kept")
            return 1
        store.backup(cfg, source)
        os.remove(source)
    return 0


def ack_targets(cfg: config.Config, plan: decisionmove.Plan) -> dict[str, tuple[str, str]]:
    """Where each acknowledged `decisions/<x>.md` went or goes: the planned moves (their owner text
    after the move) and the orphans, whose file is gone and whose owner exists."""
    targets = {m.source: (m.owner.rel, m.owner_text) for m in plan.moves}
    view = DiskVault(cfg)
    profiles = [n[:-3] for n in view.names("projects") if not view.hidden(f"projects/{n}")]
    tickets = repos.tickets_from(cfg)
    for rec in privacyack.read(cfg):
        rel = str(rec["file"])
        if rel in targets or not rel.startswith("decisions/") or view.exists(rel):
            continue
        found = decisionmove.owner_of(rel, profiles, tickets)
        if found is not None and view.exists(found.rel):
            targets[rel] = (found.rel, view.read(found.rel))
    return targets


def rehomed_acks(cfg: config.Config, plan: decisionmove.Plan) -> tuple[str, str, int, int] | None:
    """(ack file path, its new text, moved, stayed) of the re-homing; None without an ack file."""
    path = store.vault_file(cfg, ack_core.ACK_FILE)
    if path is None or not os.path.isfile(path):
        return None
    new, moved, stayed = ack_core.rehome(
        store.read_text(path), ack_targets(cfg, plan), cfg.privacy_prefixes()
    )
    return path, new, moved, stayed


def acks_to_move(cfg: config.Config, plan: decisionmove.Plan) -> int:
    """How many acknowledgements `carry_acks` would move; reads, never writes."""
    found = rehomed_acks(cfg, plan)
    return found[2] if found else 0


def carry_acks(
    cfg: config.Config, plan: decisionmove.Plan, dry_run: bool, say: Callable[[str], None]
) -> int:
    """Re-home the privacy acknowledgements of moved decision files (backup, atomic write)."""
    found = rehomed_acks(cfg, plan)
    if found is None:
        return 0
    path, new, moved, stayed = found
    if moved:
        verb = "would move" if dry_run else "moved"
        say(f"decisions: {moved} privacy acknowledgements {verb} with their lines")
        if not dry_run:
            store.backup(cfg, path)
            store.write_text(path, new)
            if fs.read_bytes(path) != new.encode("utf-8"):
                say(f"decisions: {ack_core.ACK_FILE} did not read back as written")
                return 1
    if stayed:
        why = "their line is not in the owner file"
        say(f"decisions: {stayed} privacy acknowledgements stay ({why})")
    return 0
