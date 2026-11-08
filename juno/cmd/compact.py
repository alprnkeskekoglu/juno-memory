import argparse
import os
from collections.abc import Mapping
from typing import Any

from juno import config
from juno.adapters import fs, ledgerio, store
from juno.cmd import write
from juno.core import compact

MAX_CARDS = 20
ARCHIVE = "archive/handoffs"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true")


def vault_file(cfg: config.Config, rel: str) -> str:
    return os.path.join(cfg.vault, *rel.split("/"))


def safe(cfg: config.Config, rel: str) -> bool:
    return store.vault_file(cfg, rel) is not None


def names(cfg: config.Config, directory: str) -> list[str]:
    try:
        listed = os.listdir(vault_file(cfg, directory))
    except OSError:
        return []
    return sorted(n for n in listed if n.endswith(".md") and not n.startswith("."))


def put(cfg: config.Config, rel: str, text: str, mode: int | None = None) -> None:
    path = vault_file(cfg, rel)
    store.backup(cfg, path)
    fs.atomic_write(path, text.encode("utf-8"), mode)


def compact_one(cfg: config.Config, rel: str, result: dict[str, Any]) -> None:
    original = store.read_text(vault_file(cfg, rel))
    preamble, blocks = compact.split_blocks(original)
    if len(blocks) <= MAX_CARDS:
        return
    if preamble + "".join(b.text for b in blocks) != original:
        result["skipped"][rel] = "split would not reproduce the file"
        return
    found = compact.plan(preamble, blocks, MAX_CARDS)
    if found is None:
        return
    source = os.path.basename(rel)
    planned: dict[str, tuple[str, list[str]]] = {}
    for month, texts in found.moved.items():
        target = f"{ARCHIVE}/{month}.md"
        if not safe(cfg, target):
            result["skipped"][rel] = f"unsafe path: {target}"
            return
        text = compact.ensure_private(store.read_text(vault_file(cfg, target)), f"Archive {month}")
        fresh = compact.fresh_blocks(text, texts)
        if fresh:
            text = compact.append_section(text, source, fresh)
        planned[month] = (text, texts)
    for month, (text, texts) in planned.items():
        put(cfg, f"{ARCHIVE}/{month}.md", text, 0o600)
        name = f"{ARCHIVE}/{month}.md"
        result["archived"][name] = result["archived"].get(name, 0) + len(texts)
    missing = [
        m
        for m, (_, texts) in planned.items()
        if not compact.archive_holds(store.read_text(vault_file(cfg, f"{ARCHIVE}/{m}.md")), texts)
    ]
    if missing:
        result["skipped"][rel] = f"archive verification failed for {', '.join(missing)}"
        return
    if store.read_text(vault_file(cfg, rel)) != original:
        result["skipped"][rel] = "file changed during compact; archive kept, handoff untouched"
        return
    put(cfg, rel, found.kept)
    result["moved"][rel] = found.count


def fix_private(cfg: config.Config, result: dict[str, Any]) -> None:
    for name in names(cfg, ARCHIVE):
        rel = f"{ARCHIVE}/{name}"
        if not safe(cfg, rel):
            result["skipped"][rel] = "unsafe path"
            continue
        text = store.read_text(vault_file(cfg, rel))
        fixed = compact.ensure_private(text, name[:-3])
        if fixed != text:
            put(cfg, rel, fixed, 0o600)
            result["private_fixed"].append(rel)


def run_compact(cfg: config.Config) -> dict[str, Any]:
    result: dict[str, Any] = {"moved": {}, "archived": {}, "private_fixed": [], "skipped": {}}
    for name in names(cfg, "handoffs"):
        rel = f"handoffs/{name}"
        if not safe(cfg, rel):
            result["skipped"][rel] = "unsafe path"
            continue
        try:
            with write.locked(cfg, write.VIEW_LOCK_SECONDS):
                compact_one(cfg, rel, result)
        except write.Refused:
            result["skipped"][rel] = "vault lock busy"
        except OSError as error:
            result["skipped"][rel] = f"io error: {error}"
    try:
        with write.locked(cfg, write.VIEW_LOCK_SECONDS):
            fix_private(cfg, result)
    except write.Refused:
        result["skipped"][ARCHIVE] = "vault lock busy"
    return result


def summary(result: dict[str, Any]) -> str:
    lines = [f"moved {rel}: {n}" for rel, n in sorted(result["moved"].items())]
    lines += [f"archived {rel}: {n}" for rel, n in sorted(result["archived"].items())]
    lines += [f"private fixed: {rel}" for rel in result["private_fixed"]]
    lines += [f"skipped {rel}: {why}" for rel, why in sorted(result["skipped"].items())]
    return "Juno compact:\n" + ("\n".join(lines) if lines else "nothing to move")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    def body(cfg: config.Config) -> int:
        seg = write.guard(cfg)
        result = run_compact(cfg)
        ledgerio.record(cfg, seg, {"ev": "compact"})
        write.emit(args, result, summary(result))
        return 0

    return write.execute(args, env, body)
