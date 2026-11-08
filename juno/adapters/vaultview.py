import json
import os
import re
import time
from collections.abc import Collection

from juno import config
from juno.adapters import fs, store
from juno.core import retire, vaultfmt

RETIRED_FILE = "retired.json"
STALE_FILE = "stale.json"
STALE_CACHE_VERSION = 1
STALE_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
STALE_KEY_RE = re.compile("(?:knowledge|projects)/[^/\x00-\x1f\x7f-\x9f  ]+\\.md")
SKIP_DIRS = frozenset({".git", ".obsidian", "node_modules"})


def read_retired(state: str) -> set[str] | None:
    path = os.path.join(state, RETIRED_FILE)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
        if not isinstance(doc, dict) or doc.get("v") != 1:
            return set()
        return {e["path"] for e in doc["retired"] if isinstance(e["path"], str)}
    except OSError, ValueError, KeyError, TypeError:
        return set()


def scan_metas(vault: str) -> dict[str, retire.FileMeta]:
    metas: dict[str, retire.FileMeta] = {}
    for base, dirs, files in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if not name.endswith(".md") or name.startswith("."):
                continue
            path = os.path.join(base, name)
            if os.path.islink(path):
                continue
            rel = os.path.relpath(path, vault).replace(os.sep, "/")
            if vaultfmt.kind_of(rel) in vaultfmt.NEVER_INJECTED_KINDS:
                continue
            metas[rel] = retire.meta_of(store.read_text(path))
    return metas


def write_retired(state: str, resolution: retire.Resolution) -> None:
    entries = resolution.entries()
    target = os.path.join(state, RETIRED_FILE)
    try:
        with open(target, encoding="utf-8") as handle:
            old = json.load(handle)
        if isinstance(old, dict) and old.get("v") == 1 and old.get("retired") == entries:
            return
    except OSError, ValueError:
        pass
    doc = {
        "v": 1,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "retired": entries,
    }
    fs.atomic_write(target, (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode(), 0o600)


def sync_retired(cfg: config.Config) -> retire.Resolution:
    resolution = retire.resolve(scan_metas(cfg.vault))
    write_retired(store.state_dir(cfg), resolution)
    return resolution


class DiskVault:
    def __init__(self, cfg: config.Config) -> None:
        self.cfg = cfg
        self.root = cfg.vault
        self.retired = read_retired(cfg.state)
        self.stale_data: dict[str, object] | None = None

    def path(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/"))

    def read(self, rel: str) -> str:
        return store.read_text(self.path(rel))

    def exists(self, rel: str) -> bool:
        return os.path.exists(self.path(rel))

    def names(self, directory: str) -> list[str]:
        try:
            listed = os.listdir(self.path(directory))
        except OSError:
            return []
        return sorted(n for n in listed if n.endswith(".md") and not n.startswith("."))

    def mtime(self, rel: str) -> float:
        try:
            return os.path.getmtime(self.path(rel))
        except OSError:
            return 0.0

    def hidden(self, rel: str, text: str | None = None) -> bool:
        body = self.read(rel) if text is None else text
        return vaultfmt.hidden_reason(rel, body, self.retired) is not None

    def stale_cache(self) -> dict[str, object]:
        if self.stale_data is None:
            data = store.read_json_dict(os.path.join(self.cfg.state, STALE_FILE))
            valid = data.get("v") == STALE_CACHE_VERSION and isinstance(data.get("files"), dict)
            self.stale_data = data if valid else {}
        return self.stale_data

    def stale_since(self, rel: str) -> str | None:
        if not STALE_KEY_RE.fullmatch(rel):
            return None
        files = self.stale_cache().get("files")
        entry = files.get(rel) if isinstance(files, dict) else None
        if not isinstance(entry, dict) or entry.get("stale") is not True:
            return None
        since, sig = entry.get("since"), entry.get("sig")
        valid_sig = isinstance(sig, list) and len(sig) == 2 and all(type(v) is int for v in sig)
        if not isinstance(since, str) or not STALE_DATE_RE.fullmatch(since) or not valid_sig:
            return None
        return since if sig == store.file_sig(self.path(rel)) else None

    def stale_mark(self, rel: str) -> str:
        since = self.stale_since(rel)
        return f" (may be stale since {since})" if since else ""

    def linked_titles(self, kind: str, key: str, exclude: Collection[str] = ()) -> str:
        from juno.adapters import linksio

        return linksio.linked_titles(self.cfg, kind, key, self.retired, self.stale_mark, exclude)

    def linked_paths(self, kind: str, key: str, exclude: Collection[str] = ()) -> list[str]:
        from juno.adapters import linksio

        return linksio.linked_paths(self.cfg, kind, key, self.retired, exclude)
