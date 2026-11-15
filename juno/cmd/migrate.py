import argparse
import os
import shutil
import time
from collections.abc import Mapping
from typing import cast

from juno import config
from juno.adapters import fs, migratesteps, protocolio, store, vault
from juno.adapters.vaultview import DiskVault
from juno.cmd.common import say
from juno.core import cardcopies, cardfmt, cardsettle, fold, migrate, vaultfmt


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="list the steps, change nothing")
    parser.add_argument(
        "--protocol",
        action="store_true",
        help="bring protocol/*.md up to the plugin's templates (only files never edited here)",
    )
    parser.add_argument(
        "--cards",
        action="store_true",
        help="drop extra copies of a card, and move a card to the repo file it names",
    )


def sync_protocol(cfg: config.Config, dry_run: bool) -> int:
    states = protocolio.scan(cfg)
    todo = [(n, s) for n, s in states if s in ("outdated", "missing")]
    for name, state in states:
        if state == "modified":
            say(f"protocol: protocol/{name} was edited here; left alone")
    for name, state in todo:
        verb = "update" if state == "outdated" else "restore"
        if dry_run:
            say(f"protocol: would {verb} protocol/{name}")
            continue
        protocolio.update(cfg, name)
        say(f"protocol: {verb}d protocol/{name} (previous copy saved under {cfg.state}/backups)")
    if not todo:
        say("protocol: files are up to date")
    return 0


def move_misfiled(
    texts: dict[str, str],
    cards: list[dict[str, object]],
    profiles: list[str],
    hidden: set[str],
) -> list[tuple[str, str, str]]:
    """Move each misfiled card in `texts` to the file of the first repo it names.

    Returns (source, destination, segment) per moved card; `texts` is updated in place.
    """
    moved: list[tuple[str, str, str]] = []
    for card in cardcopies.misfiled(cards, profiles):
        repos = [fold.safe_name(r) for r in cast(list[str], card["repos"])]
        dest = f"handoffs/{repos[0]}.md" if repos else ""
        src, seg = str(card["path"]), str(card["seg"])
        if (
            not cardfmt.HANDOFF_NAME_RE.fullmatch(dest.removeprefix("handoffs/"))
            or dest == src
            or dest in hidden
        ):
            say(f"cards: {src}: card {seg} names no repo file it can move to")
            continue
        found = cardsettle.spans(texts[src], seg)
        have = texts.get(dest, "")
        texts[src] = cardsettle.cut(texts[src], found)
        if not cardsettle.spans(have, seg):
            for span in found:
                have = cardsettle.insert_card(have, span.text, span.stamp, repos[0])
            texts[dest] = have
        moved.append((src, dest, seg))
    return moved


def drop_card_copies(cfg: config.Config, dry_run: bool) -> int:
    view = DiskVault(cfg)
    texts: dict[str, str] = {}
    hidden: set[str] = set()
    for name in view.names("handoffs"):
        rel = f"handoffs/{name}"
        text = view.read(rel)
        if view.hidden(rel, text):
            hidden.add(rel)
        else:
            texts[rel] = text
    original = dict(texts)

    def parse() -> list[dict[str, object]]:
        return [c for rel, text in sorted(texts.items()) for c in vaultfmt.parse_cards(text, rel)]

    profiles = [n[:-3] for n in view.names("projects")]
    by_file: dict[str, list[str]] = {}
    for card in cardcopies.extra_copies(parse(), profiles):
        by_file.setdefault(str(card["path"]), []).append(str(card["seg"]))
    for rel, found in sorted(by_file.items()):
        segs = list(dict.fromkeys(found))
        drop = [span for seg in segs for span in cardsettle.spans(texts[rel], seg)]
        texts[rel] = cardsettle.cut(texts[rel], drop)
        say(f"cards: {'would drop' if dry_run else 'dropped'} {len(segs)} copies from {rel}")
    moved = move_misfiled(texts, parse(), profiles, hidden)
    flows: dict[tuple[str, str], list[str]] = {}
    for src, dest, seg in moved:
        flows.setdefault((src, dest), []).append(seg)
    for (src, dest), segs in sorted(flows.items()):
        word = "card" if len(segs) == 1 else "cards"
        verb = "would move" if dry_run else "moved"
        say(f"cards: {verb} {len(segs)} {word} from {src} to {dest} ({', '.join(segs)})")
    if not by_file and not moved:
        say("cards: no extra copies")
    if dry_run:
        return 0
    changed = [rel for rel in sorted(texts) if texts[rel] != original.get(rel)]
    gained = {dest for _, dest, _ in moved}
    # A destination is written and read back before any source loses its card.
    for rel in sorted(changed, key=lambda r: r not in gained):
        path = os.path.join(cfg.vault, *rel.split("/"))
        store.backup(cfg, path)
        store.write_text(path, texts[rel])
        if rel in gained and store.read_text(path) != texts[rel]:
            say(f"cards: {rel} did not read back as written; stopped before removing from sources")
            return 1
    return 0


def run(
    args: argparse.Namespace,
    env: Mapping[str, str],
    steps: Mapping[int, migrate.Step] | None = None,
    target: int | None = None,
) -> int:
    steps = migratesteps.STEPS if steps is None else steps
    target = migrate.CODE_SCHEMA_VERSION if target is None else target
    cfg = config.load(env)
    if vault.status(cfg.vault) != "juno":
        say(f"Juno: no Juno vault at {cfg.vault}; run juno init")
        return 2
    if getattr(args, "protocol", False):
        return sync_protocol(cfg, args.dry_run)
    if getattr(args, "cards", False):
        return drop_card_copies(cfg, args.dry_run)
    current = vault.schema_version(cfg.vault)
    if current is None:
        say("Juno: juno-brain.toml has no schema_version")
        return 2
    if current > target:
        say(f"Juno: vault schema {current} is newer than this Juno ({target}); update Juno")
        return 2
    try:
        wanted = migrate.plan(current, target, steps)
    except ValueError as error:
        say(f"Juno: {error}")
        return 2
    if not wanted:
        say(f"migrate: vault is at schema {current}; nothing to do")
        return 0
    toml_path = os.path.join(cfg.vault, vault.TOML_NAME)
    for version in wanted:
        say(f"migrate: schema {version} -> {version + 1}" + (" (dry run)" if args.dry_run else ""))
        if args.dry_run:
            continue
        stamp = time.strftime("%Y%m%dT%H%M%S")
        backup = os.path.join(cfg.state, "backups", f"migrate-{stamp}-v{version}")
        fs.ensure_private_dir(os.path.dirname(backup))
        shutil.copytree(cfg.vault, backup, symlinks=True)
        steps[version](cfg.vault)
        text = (fs.read_bytes(toml_path) or b"").decode("utf-8")
        fs.atomic_write(toml_path, migrate.write_schema(text, version + 1).encode("utf-8"))
        say(f"migrate: now at schema {version + 1} (backup {backup})")
    return 0
