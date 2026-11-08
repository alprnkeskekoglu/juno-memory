import datetime
import os
import time
from typing import Any

from juno import config
from juno.adapters import fs, git, repos, store, vaultview
from juno.core import stale, vaultfmt

STALE_FILE = vaultview.STALE_FILE


def inside(path: str, root: str) -> bool:
    return store.is_within(os.path.realpath(path), os.path.realpath(root))


class Scan:
    def __init__(self, cfg: config.Config) -> None:
        self.cfg = cfg
        self.deadline = time.monotonic() + stale.GIT_BUDGET_SECONDS
        self.exhausted = False
        self.memo: dict[tuple[str, str, datetime.date], int | None] = {}

    def repo_dir(self, name: str) -> str | None:
        return repos.repo_dir(self.cfg, name)

    def commits(self, repo_dir: str, path: str, since: datetime.date) -> int | None:
        key = (repo_dir, path, since)
        if key not in self.memo:
            self.memo[key] = self.count(repo_dir, path, since)
        return self.memo[key]

    def count(self, repo_dir: str, path: str, since: datetime.date) -> int | None:
        if time.monotonic() > self.deadline:
            self.exhausted = True
            return None
        out = git.run(
            [
                "log",
                f"--since={since.isoformat()}T23:59:59",
                "--format=%H",
                f"--max-count={stale.MAX_COMMITS}",
                "--",
                ":(literal)" + path,
            ],
            repo_dir,
        )
        return None if out is None else len(out.splitlines())

    def resolve(
        self, token: str, known: list[str], entry: stale.Entry
    ) -> tuple[str, str, str] | None:
        for repo, path in stale.candidates(token, known, lambda n: self.repo_dir(n) is not None):
            repo_dir = self.repo_dir(repo)
            if repo_dir is None:
                if repo not in entry["missing_repos"]:
                    entry["missing_repos"].append(repo)
                continue
            target = os.path.join(repo_dir, path)
            if inside(target, repo_dir) and os.path.exists(target):
                return repo, repo_dir, path
        return None

    def check(self, token: str, known: list[str], since: datetime.date, entry: stale.Entry) -> None:
        hit = self.resolve(token, known, entry)
        if hit is None:
            entry["missing_paths"].append(token)
            return
        repo, repo_dir, path = hit
        if "/" not in path.strip("/") and os.path.isdir(os.path.join(repo_dir, path)):
            return
        count = self.commits(repo_dir, path, since)
        if count is None:
            entry["unchecked"].append(token)
        elif count:
            entry["drift"].append({"repo": repo, "path": path, "commits": count})


def scan_file(
    scan: Scan,
    kind: str,
    relpath: str,
    path: str,
    days: int,
    today: datetime.date,
    retired: set[str] | None,
) -> stale.Entry | None:
    st = os.stat(path, follow_symlinks=False)
    text = store.read_text(path)
    if vaultfmt.hidden_reason(relpath, text, retired) is not None:
        return None
    fields, body = vaultfmt.parse_frontmatter(text)
    since, source = stale.baseline(fields, st.st_mtime)
    entry = stale.new_entry(
        kind, relpath, since, source, (today - since).days, days, [st.st_size, st.st_mtime_ns]
    )
    known = stale.file_repos(kind, fields, os.path.basename(path))
    for token in stale.path_tokens(body):
        scan.check(token, known, since, entry)
    return stale.finish_entry(entry)


def vault_files(vault: str) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for directory, kind in stale.SCAN_DIRS:
        root = os.path.join(vault, directory)
        try:
            names = sorted(
                n for n in os.listdir(root) if n.endswith(".md") and not n.startswith(".")
            )
        except FileNotFoundError, NotADirectoryError:
            continue
        for name in names:
            path = os.path.join(root, name)
            if os.path.isfile(path) and not os.path.islink(path):
                found.append((kind, f"{directory}/{name}", path))
    return found


def analyze(cfg: config.Config, days: int, today: datetime.date | None = None) -> dict[str, Any]:
    today = today or datetime.date.today()
    scan = Scan(cfg)
    retired = vaultview.read_retired(cfg.state)
    entries: list[stale.Entry] = []
    started = time.monotonic()
    for kind, relpath, path in vault_files(cfg.vault):
        try:
            entry = scan_file(scan, kind, relpath, path, days, today, retired)
        except OSError:
            continue
        if entry:
            entries.append(entry)
    return stale.report_of(days, today, entries, scan.exhausted, time.monotonic() - started)


def write_cache(cfg: config.Config, report: dict[str, Any]) -> str:
    import json

    path = os.path.join(store.state_dir(cfg), STALE_FILE)
    text = json.dumps(stale.cache_of(report, int(time.time())), ensure_ascii=False, sort_keys=True)
    fs.atomic_write(path, (text + "\n").encode("utf-8"), 0o600)
    return path


def run(cfg: config.Config, days: int) -> dict[str, Any]:
    report = analyze(cfg, days)
    report["cache"] = write_cache(cfg, report)
    return report
