"""Walk Claude Code's native memory dirs (~/.claude/projects/<slug>/memory/*.md), read only."""

import os
import stat
from dataclasses import dataclass

from juno import config
from juno.adapters import cardjob, fs, repos
from juno.core import nativememory

MAX_BYTES = 64 * 1024
MAX_FILES = 500


@dataclass(frozen=True)
class Found:
    slug: str
    repo: str | None
    note: nativememory.Note

    @property
    def source(self) -> str:
        return nativememory.source(self.slug, self.note.file)


def repo_slugs(cfg: config.Config) -> dict[str, str]:
    found: dict[str, str] = {}
    for root in cfg.repo_roots():
        real_root = os.path.realpath(root)
        try:
            names = sorted(os.listdir(real_root))
        except OSError:
            continue
        for entry in names:
            hit = repos.root_repo(cfg, os.path.join(real_root, entry), real_root)
            if hit:
                found[nativememory.project_slug(os.path.realpath(hit[1]))] = hit[0]
    return found


def memory_files(directory: str) -> list[str]:
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    out: list[str] = []
    for name in names:
        path = os.path.join(directory, name)
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if name.endswith(".md") and stat.S_ISREG(info.st_mode) and info.st_size <= MAX_BYTES:
            out.append(name)
    return out[:MAX_FILES]


def scan(cfg: config.Config) -> list[Found]:
    root = cardjob.projects_root(cfg)
    try:
        slugs = sorted(os.listdir(root))
    except OSError:
        return []
    mapping = repo_slugs(cfg)
    found: list[Found] = []
    for slug in slugs:
        directory = os.path.join(root, slug, "memory")
        if not os.path.isdir(directory) or os.path.islink(directory):
            continue
        repo = nativememory.match_repo(slug, mapping)
        for name in memory_files(directory):
            raw = fs.read_bytes(os.path.join(directory, name))
            note = nativememory.parse(name, (raw or b"").decode("utf-8", "replace"))
            if note:
                found.append(Found(slug, repo, note))
    return found


def summary(found: list[Found]) -> str:
    projects = len({f.slug for f in found})
    notes = f"{len(found)} note" + ("" if len(found) == 1 else "s")
    return f"{notes} in {projects} project" + ("" if projects == 1 else "s")
