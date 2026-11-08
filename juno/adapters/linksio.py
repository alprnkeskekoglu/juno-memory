import json
import os
from collections.abc import Callable, Collection, Iterator
from typing import Any

from juno import config
from juno.adapters import fs, store
from juno.core import fold, inject, links

CACHE_FILE = "links.json"
MAX_READ_BYTES = 1 << 20


def walk(vault: str) -> Iterator[tuple[str, str, list[int]]]:
    stack = [("", vault)]
    while stack:
        prefix, directory = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_symlink():
                    continue
                relpath = prefix + entry.name
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.casefold() != "archive":
                        stack.append((relpath + "/", entry.path))
                elif entry.name.endswith(".md") and entry.is_file(follow_symlinks=False):
                    st = entry.stat(follow_symlinks=False)
                    yield relpath, entry.path, [st.st_size, st.st_mtime_ns]
            except OSError:
                continue


def parse_path(relpath: str, path: str, sig: list[int]) -> dict[str, Any]:
    try:
        with open(path, "rb") as handle:
            text = handle.read(MAX_READ_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return {"sig": None, "title": links.title_of(relpath, ""), "hidden": False, "links": []}
    return links.parse_file(relpath, text, sig)


def cache_path(cfg: config.Config) -> str:
    return os.path.join(cfg.state, CACHE_FILE)


def load_cache(cfg: config.Config) -> dict[str, Any]:
    data = store.read_json_dict(cache_path(cfg))
    files = data.get("files")
    if data.get("v") != links.CACHE_VERSION or not isinstance(files, dict):
        return {}
    return files


def link_map(cfg: config.Config) -> dict[str, Any]:
    cached = load_cache(cfg)
    files: dict[str, Any] = {}
    dirty = False
    for relpath, path, sig in walk(cfg.vault):
        entry = cached.get(relpath)
        if not isinstance(entry, dict) or not links.valid_entry(entry) or entry.get("sig") != sig:
            entry, dirty = parse_path(relpath, path, sig), True
        files[relpath] = entry
    if dirty or len(files) != len(cached):
        save_cache(cfg, files)
    return files


def save_cache(cfg: config.Config, files: dict[str, Any]) -> None:
    try:
        text = json.dumps({"v": links.CACHE_VERSION, "files": files}, ensure_ascii=False)
        fs.ensure_private_dir(cfg.state)
        fs.atomic_write(cache_path(cfg), text.encode("utf-8"), 0o600)
    except OSError as error:
        store.log_error(cfg, "links-cache", error)


def anchored(
    cfg: config.Config, kind: str, key: str, retired: Collection[str] | None
) -> tuple[str, dict[str, Any]] | None:
    if retired is None:
        return None
    files = link_map(cfg)
    anchor = links.anchor_of(kind, key, fold.safe_name(key))
    return (anchor, files) if anchor in files else None


def linked_titles(
    cfg: config.Config,
    kind: str,
    key: str,
    retired: Collection[str] | None,
    mark: Callable[[str], str],
    exclude: Collection[str] = (),
) -> str:
    found = anchored(cfg, kind, key, retired)
    if not found or retired is None:
        return ""
    return links.linked_lines(found[0], found[1], retired, mark, inject.neutralize, exclude)


def linked_paths(
    cfg: config.Config,
    kind: str,
    key: str,
    retired: Collection[str] | None,
    exclude: Collection[str] = (),
) -> list[str]:
    found = anchored(cfg, kind, key, retired)
    if not found or retired is None:
        return []
    return links.linked_paths(found[0], found[1], retired, exclude)


def doctor_report(cfg: config.Config) -> list[tuple[str, str]]:
    return links.link_report(link_map(cfg))
