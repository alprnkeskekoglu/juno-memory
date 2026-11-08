import datetime
import os
import time
from dataclasses import dataclass, field
from typing import Any

from juno import config
from juno.adapters import git, repos, store, vaultview
from juno.core import redact, stale, vaultfmt
from juno.core import stalefix as core

FETCH_FRESH_DAYS = 7
EXPORT_CAP_BYTES = 200 * 1024 * 1024
MAX_FACT_COMMITS = 20
MAX_RENAMES = 2000
GIT_SECONDS = 30.0


@dataclass
class Pin:
    repo: str
    dir: str
    ref: str
    sha: str
    fetch: str


def fetch_age_days(repo_dir: str) -> float | None:
    dirs = repos.git_dirs(repo_dir)
    if not dirs:
        return None
    try:
        return (time.time() - os.stat(os.path.join(dirs[1], "FETCH_HEAD")).st_mtime) / 86400
    except OSError:
        return None


def pin_repo(cfg: config.Config, name: str) -> Pin | str:
    directory = repos.repo_dir(cfg, name)
    if directory is None:
        return "repo missing"
    order = cfg.get_list("stale", "branch_order")
    if not repos.has_origin(directory):
        local = repos.local_default_ref(directory, order)
        if local is None:
            return "no default branch"
        return Pin(name, directory, local[0], local[1], "no remote")
    fetch = "skipped"
    if cfg.get_bool("stale", "fetch"):
        done = git.result(["fetch", "--quiet", "origin"], directory, 60)
        if done is not None and done.returncode == 0:
            fetch = "ok"
        else:
            age = fetch_age_days(directory)
            if age is None or age > FETCH_FRESH_DAYS:
                return "fetch failed"
            fetch = f"failed, last fetch {age:.1f} days ago"
    found = repos.default_ref(directory, order)
    if found is None:
        return "no origin default branch"
    return Pin(name, directory, found[0], found[1], fetch)


def exists_at(pin: Pin, path: str) -> bool:
    done = git.result(["cat-file", "-e", f"{pin.sha}:{path}"], pin.dir, GIT_SECONDS)
    return done is not None and done.returncode == 0


def blob_lines(pin: Pin, path: str) -> int | None:
    done = git.result(["cat-file", "blob", f"{pin.sha}:{path}"], pin.dir, GIT_SECONDS)
    if done is None or done.returncode != 0:
        return None
    return len(done.stdout.splitlines())


def commits_since(pin: Pin, path: str, since: datetime.date) -> list[str]:
    done = git.result(
        [
            "log",
            pin.sha,
            f"--since={since.isoformat()}T23:59:59",
            "--format=%h %s",
            f"--max-count={MAX_FACT_COMMITS}",
            "--",
            ":(literal)" + path,
        ],
        pin.dir,
        GIT_SECONDS,
    )
    return done.stdout.splitlines() if done is not None and done.returncode == 0 else []


def renames_since(pin: Pin, since: datetime.date) -> dict[str, str]:
    done = git.result(
        [
            "log",
            pin.sha,
            f"--since={since.isoformat()}T23:59:59",
            "--diff-filter=R",
            "-M",
            "--name-status",
            "--format=",
            f"--max-count={MAX_RENAMES}",
        ],
        pin.dir,
        GIT_SECONDS,
    )
    found: dict[str, str] = {}
    for line in done.stdout.splitlines() if done is not None and done.returncode == 0 else []:
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            found.setdefault(parts[1], parts[2])
    return found


@dataclass
class FileScan:
    rel: str
    baseline: str
    age_days: int
    repos: list[str]
    pins: dict[str, Pin]
    facts: dict[str, dict[str, Any]]
    candidates: list[core.Candidate]
    truncated: bool
    drift: int
    lines: list[str] = field(default_factory=list)


class Probe:
    def __init__(self, cfg: config.Config) -> None:
        self.cfg = cfg
        self.pins: dict[str, Pin | str] = {}
        self.renames: dict[tuple[str, datetime.date], dict[str, str]] = {}

    def pin(self, name: str) -> Pin | str:
        if name not in self.pins:
            self.pins[name] = pin_repo(self.cfg, name)
        return self.pins[name]

    def rename_map(self, pin: Pin, since: datetime.date) -> dict[str, str]:
        key = (pin.repo, since)
        if key not in self.renames:
            self.renames[key] = renames_since(pin, since)
        return self.renames[key]

    def fact(self, token: str, known: list[str], since: datetime.date) -> dict[str, Any]:
        tried: list[tuple[str, str]] = []
        unchecked = False
        for name, path in stale.candidates(
            token, known, lambda n: repos.repo_dir(self.cfg, n) is not None
        ):
            pin = self.pin(name)
            if isinstance(pin, str):
                unchecked = True
                continue
            tried.append((name, path))
            if exists_at(pin, path):
                return {
                    "token": token,
                    "repo": name,
                    "path": path,
                    "exists": True,
                    "unchecked": False,
                    "commits": commits_since(pin, path, since),
                }
        fact: dict[str, Any] = {
            "token": token,
            "exists": False,
            "commits": [],
            "unchecked": unchecked,
        }
        if tried:
            fact["repo"], fact["path"] = tried[0]
            for name, path in tried:
                pin = self.pin(name)
                assert isinstance(pin, Pin)
                moved = self.rename_map(pin, since).get(path)
                if moved:
                    fact["renamed_to"] = moved
        else:
            fact["unchecked"] = True
        return fact


def scan_file(
    cfg: config.Config,
    probe: Probe,
    kind: str,
    rel: str,
    path: str,
    days: int,
    today: datetime.date,
    retired: set[str] | None,
) -> FileScan | str:
    text = store.read_text(path)
    reason = vaultfmt.hidden_reason(rel, text, retired)
    if reason is not None:
        return reason
    fields, body = vaultfmt.parse_frontmatter(text)
    since, _ = stale.baseline(fields, os.stat(path).st_mtime)
    tokens = stale.path_tokens(body)
    if not tokens:
        return "no code anchor"
    known = stale.file_repos(kind, fields, os.path.basename(path))
    facts = {t: probe.fact(t, known, since) for t in tokens}
    drifted = [t for t, f in facts.items() if f["exists"] and f["commits"]]
    missing = [t for t, f in facts.items() if not f["exists"] and not f["unchecked"]]
    if not drifted and not missing:
        unchecked = any(f["unchecked"] for f in facts.values())
        return "repo missing" if unchecked else "no drift"
    age = (today - since).days
    if not drifted and age <= days:
        return "fresh"
    lines = text.splitlines()
    found, skipped = core.candidate_lines(
        lines, [*drifted, *missing], cfg.get_int("stale", "max_lines_per_file")
    )
    safe = [c for c in found if redact.redact(c.text) == c.text]
    if not safe:
        return "no code anchor"
    covered = {t for c in safe for t in c.tokens}
    pins = {n: p for n in known if isinstance(p := probe.pin(n), Pin)}
    for fact in facts.values():
        if fact.get("repo") and isinstance(p := probe.pin(fact["repo"]), Pin):
            pins[p.repo] = p
    return FileScan(
        rel,
        since.isoformat(),
        age,
        known,
        pins,
        facts,
        safe,
        bool(skipped) or len(safe) != len(found) or not set(drifted + missing) <= covered,
        sum(len(facts[t]["commits"]) for t in drifted),
        lines,
    )


def select(
    cfg: config.Config, days: int, today: datetime.date, only: str | None
) -> tuple[list[FileScan], list[dict[str, str]], Probe]:
    from juno.adapters import stalescan

    probe = Probe(cfg)
    retired = vaultview.read_retired(cfg.state)
    chosen: list[FileScan] = []
    skipped: list[dict[str, str]] = []
    for kind, rel, path in stalescan.vault_files(cfg.vault):
        if only and rel != only:
            continue
        try:
            found = scan_file(cfg, probe, kind, rel, path, days, today, retired)
        except OSError:
            continue
        if isinstance(found, str):
            skipped.append({"file": rel, "reason": found})
        else:
            chosen.append(found)
    chosen.sort(key=lambda s: (-s.drift, -s.age_days, s.rel))
    return chosen, skipped, probe


def absent_paths(scan: FileScan, cand: core.Candidate) -> set[tuple[str, str]]:
    return {
        (str(f["repo"]), str(f["path"]))
        for t in stale.path_tokens(cand.text)
        if (f := scan.facts.get(t)) and not f["exists"] and f.get("repo")
    }


def gone_problem(scan: FileScan, cand: core.Candidate) -> str | None:
    tokens = stale.path_tokens(cand.text)
    for token in tokens:
        fact = scan.facts.get(token)
        if fact is None or fact["exists"] or fact["unchecked"] or fact.get("renamed_to"):
            return "a path on the line still exists, moved or was not checked"
    return None if tokens else "no path on the line"


def evidence_problem(scan: FileScan, cand: core.Candidate, verdict: core.Verdict) -> str | None:
    for ev in verdict.evidence:
        pin = scan.pins.get(ev.repo)
        parts = ev.path.split("/")
        if (
            pin is None
            or not pin.sha.startswith(ev.sha)
            or ".." in parts
            or ev.path.startswith("/")
        ):
            continue
        if verdict.verdict == "GONE" and (ev.repo, ev.path) in absent_paths(scan, cand):
            return None
        count = blob_lines(pin, ev.path)
        if count is not None and 1 <= ev.line <= count:
            return None
    return "no evidence resolves to a line of a file at the pinned commit"


def first_evidence(scan: FileScan, cand: core.Candidate, verdict: core.Verdict) -> core.Evidence:
    for ev in verdict.evidence:
        pin = scan.pins.get(ev.repo)
        if pin is None or not pin.sha.startswith(ev.sha):
            continue
        if verdict.verdict == "GONE" and (ev.repo, ev.path) in absent_paths(scan, cand):
            return core.Evidence(ev.repo, pin.sha, ev.path, ev.line)
        count = blob_lines(pin, ev.path)
        if count is not None and 1 <= ev.line <= count:
            return core.Evidence(ev.repo, pin.sha, ev.path, ev.line)
    raise ValueError("no resolving evidence")


def top_level(paths: list[str]) -> list[str]:
    found: list[str] = []
    for path in paths:
        head = path.split("/", 1)[0]
        if head not in found:
            found.append(head)
    return found


def export(pin: Pin, specs: list[str], dest: str) -> bool:
    import subprocess
    import tarfile

    proc = subprocess.Popen(
        ["git", "-C", pin.dir, "archive", "--format=tar", pin.sha, "--", *specs],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=git.clean_env(),
    )
    assert proc.stdout is not None
    total = 0
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
            for member in archive:
                total += member.size
                if total > EXPORT_CAP_BYTES:
                    proc.kill()
                    return False
                archive.extract(member, dest, filter="data")
    except tarfile.TarError, OSError:
        proc.kill()
        return False
    finally:
        proc.stdout.close()
        proc.wait()
    return proc.returncode == 0


def export_scan(scan: FileScan, dest: str) -> bool:
    for name, pin in scan.pins.items():
        paths = sorted({str(f["path"]) for f in scan.facts.values() if f.get("repo") == name})
        target = os.path.join(dest, name)
        os.makedirs(target, exist_ok=True)
        heads = [h for h in top_level(paths) if exists_at(pin, h)]
        exact = [p for p in paths if exists_at(pin, p)]
        if heads and not export(pin, heads, target) and not (exact and export(pin, exact, target)):
            return False
    return True
