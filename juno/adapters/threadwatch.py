import json
import os
import re
import shutil
import time
from typing import Any

from juno import config, paths
from juno.adapters import cardjob, fs, git, queues, repos, store
from juno.core import candidates, threadscan, vaultfmt

CACHE_FILE = "thread-prs.json"
CACHE_BYTES = 256 * 1024
OPEN_RECHECK_SECONDS = 6 * 3600
OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def gh_bin(cfg: config.Config) -> str:
    named = cfg.env.get("JUNO_THREADS_GH") or cfg.get_str("threads", "gh")
    if os.sep in named:
        return named
    launch = cfg.get_str("handoff", "launch_path").replace("~", paths.home(cfg.env))
    return shutil.which(named) or shutil.which(named, path=launch) or named


def gh_view(cfg: config.Config, owner: str, repo: str, number: str) -> dict[str, str] | None:
    import subprocess

    argv = [gh_bin(cfg), "pr", "view", number, "-R", f"{owner}/{repo}", "--json", "state,title"]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=cfg.get_float("threads", "gh_timeout_seconds"),
            stdin=subprocess.DEVNULL,
            env=git.clean_env(),
        )
    except OSError, subprocess.SubprocessError:
        return None
    if result.returncode != 0:
        return {"state": "", "title": ""}
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    state = str(data.get("state") or "").upper()
    known = (*threadscan.TERMINAL, "OPEN")
    return {"state": state if state in known else "", "title": str(data.get("title") or "")[:200]}


class PrLookup:
    def __init__(self, cfg: config.Config, now: float) -> None:
        self.cfg = cfg
        self.now = now
        self.calls = 0
        self.budget = cfg.get_int("threads", "gh_calls")
        self.path = os.path.join(cardjob.cards_dir(cfg), CACHE_FILE)
        self.cache: dict[str, Any] = {}
        try:
            if os.path.getsize(self.path) <= CACHE_BYTES:
                self.cache = dict(store.read_json_dict(self.path))
        except OSError:
            pass
        self.dirty = False

    def owner(self) -> str | None:
        owner = self.cfg.get_str("threads", "gh_owner")
        return owner if OWNER_RE.match(owner) else None

    def state(self, repo: str, number: str) -> dict[str, str] | None:
        owner = self.owner()
        if owner is None:
            return None
        key = f"{owner}/{repo}#{number}"
        hit = self.cache.get(key)
        if isinstance(hit, dict) and (
            hit.get("state") in threadscan.TERMINAL
            or self.now - candidates.number(hit.get("t")) < OPEN_RECHECK_SECONDS
        ):
            return hit if hit.get("state") else None
        if self.calls >= self.budget:
            return None
        self.calls += 1
        result = gh_view(self.cfg, owner, repo, number)
        if result is not None:
            self.cache[key] = dict(result, t=self.now)
            self.dirty = True
        return result if result and result.get("state") else None

    def save(self) -> None:
        if not self.dirty:
            return
        cutoff = self.now - candidates.DECIDED_DAYS * candidates.DAY
        kept = {
            k: v
            for k, v in self.cache.items()
            if isinstance(v, dict) and candidates.number(v.get("t")) >= cutoff
        }
        fs.ensure_private_dir(cardjob.cards_dir(self.cfg))
        text = json.dumps(kept, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
        fs.atomic_write(self.path, text.encode("utf-8"), 0o600)


def task_fields(cfg: config.Config, key: str) -> vaultfmt.Fields:
    text = store.read_text(os.path.join(cfg.vault, "tasks", key.upper() + ".md"))
    return vaultfmt.parse_frontmatter(text)[0]


def signals(cfg: config.Config, line: str, now: float, lookup: PrLookup) -> list[str]:
    tickets = repos.tickets_from(cfg)

    def is_ticket(tag: str) -> bool:
        return tickets.pattern is not None and tickets.pattern.fullmatch(tag) is not None

    def status_of(key: str) -> str:
        value = task_fields(cfg, key).get("status", "")
        return value if isinstance(value, str) else ""

    def repos_of(key: str) -> list[str]:
        raw = task_fields(cfg, key).get("repos", "")
        return threadscan.task_repos(raw if isinstance(raw, str) else ", ".join(raw))

    tags, text, since = threadscan.parse(line)
    found = threadscan.ticket_signals(tags, is_ticket, status_of)
    refs = threadscan.refs(text, tags, is_ticket, repos_of)
    if refs:
        found += threadscan.pr_signals(refs, lookup.state)
    age = threadscan.age_signal(since, now, cfg.get_int("threads", "stale_days"))
    return found + ([age] if age else [])


def scan(cfg: config.Config, now: float | None = None) -> dict[str, int]:
    out = {"lines": 0, "queued": 0, "gone": 0, "gh_calls": 0}
    threads_file = os.path.join(cfg.vault, "threads.md")
    if not cfg.get_bool("threads", "scan") or not os.path.isfile(threads_file):
        return out
    stamp = now or time.time()
    lines = threadscan.thread_lines(store.read_text(threads_file))
    out["lines"] = len(lines)
    known = {i["id"] for i in queues.read(cfg, queues.THREADS)}
    lookup = PrLookup(cfg, stamp)
    found: dict[str, tuple[str, list[str]]] = {}
    for line in lines:
        cid = candidates.thread_id(line)
        if cid not in known and cid not in found:
            hits = signals(cfg, line, stamp, lookup)
            if hits:
                found[cid] = (line, hits)
    out["gh_calls"] = lookup.calls
    lookup.save()
    current = {candidates.thread_id(line) for line in lines}

    def change(queue: list[candidates.Item]) -> None:
        queue[:] = candidates.prune(queue, stamp, keep_pending=True)
        out["gone"] = candidates.mark_gone(queue, current, stamp)
        out["queued"] = candidates.add_threads(queue, found, stamp)

    queues.update(cfg, queues.THREADS, change)
    return out
