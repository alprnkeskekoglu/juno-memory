import argparse
import datetime
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from juno import config
from juno.adapters import checks, gate, modelcall, stalerun
from juno.adapters import env as envmod
from juno.adapters import stalefix as adapter
from juno.cmd import fix, forget, snapshot, write
from juno.core import cardfmt, stale, stalerow, writegate
from juno.core import stalefix as core
from juno.core.errors import Refused
from juno.core.ledger import Segment

LOCK_SECONDS = 10.0
MAX_REJECTS = 50
MAX_FAILURES = 2
Call = Callable[[config.Config, str, float, str], tuple[str, float]]


def model_call(cfg: config.Config, prompt: str, budget: float, workdir: str) -> tuple[str, float]:
    return modelcall.call_model(
        cfg,
        prompt,
        budget,
        cfg.get_str("stale", "model"),
        core.system_prompt(),
        modelcall.READ_TOOLS,
        workdir,
        cfg.get_float("stale", "timeout_seconds"),
    )


@dataclass
class Options:
    dry: bool = False
    job: bool = False
    limit: int | None = None
    max_usd: float | None = None
    only: str | None = None


@dataclass
class Outcome:
    row: dict[str, Any]
    notes: list[str] = field(default_factory=list)


def new_row(now: float, opts: Options) -> dict[str, Any]:
    today = datetime.date.fromtimestamp(now)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime(now))
    return {
        "t": round(now, 3),
        "run": f"{stamp}-{secrets.token_hex(2)}",
        "day": today.isoformat(),
        "week": stalerow.week_of(today),
        "status": "dry-run" if opts.dry else "ok",
        "dry": opts.dry,
        "files_checked": 0,
        "fixed": 0,
        "archived": 0,
        "verified": 0,
        "unsure": 0,
        "rejected": [],
        "skipped": [],
        "writes": [],
        "refs": {},
        "fetch": {},
        "cost_usd": 0.0,
        "snapshot": None,
    }


def dry_refusal(cfg: config.Config) -> str | None:
    if cfg.env.get("JUNO_DISABLE") == "1":
        return "JUNO_DISABLE is set"
    return "agent context" if envmod.agent_context(cfg.env) else None


def reject(row: dict[str, Any], rel: str, line: int, reason: str) -> None:
    if len(row["rejected"]) < MAX_REJECTS:
        row["rejected"].append({"file": rel, "line": line, "reason": reason})


def facts_of(scan: adapter.FileScan, cand: core.Candidate) -> list[dict[str, Any]]:
    return [scan.facts[t] for t in stale.path_tokens(cand.text) if t in scan.facts]


def vet(
    cfg: config.Config,
    scan: adapter.FileScan,
    verdicts: list[core.Verdict],
    rejects: list[core.Reject],
) -> list[core.Verdict]:
    cands = {c.line: c for c in scan.candidates}
    accepted: list[core.Verdict] = []
    for v in verdicts:
        cand = cands[v.line]
        problem: str | None = None
        if v.verdict != "UNSURE":
            problem = adapter.evidence_problem(scan, cand, v)
        if not problem and v.verdict == "WRONG":
            problem = core.new_text_problem(cand.text, v.new, fix.NEW_MAX)
            if not problem:
                try:
                    gate.scan_line(cfg, core.tidy_new(cand.text, v.new), "line")
                except Refused as error:
                    problem = str(error)
        if not problem and v.verdict == "GONE":
            problem = adapter.gone_problem(scan, cand)
        if problem:
            rejects.append(core.Reject(v.line, problem))
        else:
            accepted.append(v)
    return accepted


class ModelFailed(Exception):
    def __init__(self, reason: str, cost: float) -> None:
        super().__init__(reason)
        self.cost = cost


def ask_model(
    cfg: config.Config, scan: adapter.FileScan, call: Call, budget: float
) -> tuple[list[core.Verdict], list[core.Reject], float]:
    workdir = tempfile.mkdtemp(prefix="juno-stale-")
    cost = 0.0
    try:
        if not adapter.export_scan(scan, workdir):
            raise cardfmt.Failed("repository export failed")
        prompt = core.build_prompt(
            scan.rel,
            scan.baseline,
            {n: p.sha for n, p in scan.pins.items()},
            [(c, facts_of(scan, c)) for c in scan.candidates],
            "data-" + os.urandom(6).hex(),
        )
        text, cost = call(cfg, prompt, budget, workdir)
    except cardfmt.Failed as error:
        raise ModelFailed(str(error), error.cost) from None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    try:
        entries = core.parse_answer(text)
    except core.Malformed as error:
        raise ModelFailed(str(error), cost) from None
    verdicts, rejects = core.check_entries(entries, {c.line: c for c in scan.candidates})
    return vet(cfg, scan, verdicts, rejects), rejects, cost


def commit_changes(
    cfg: config.Config,
    seg: Segment,
    scan: adapter.FileScan,
    plan: core.FilePlan,
    accepted: list[core.Verdict],
    rid: str,
    day: str,
    row: dict[str, Any],
) -> None:
    extra: dict[str, object] = {"actor": stalerow.ACTOR, "run": rid}
    cands = {c.line: c for c in scan.candidates}
    clean = True
    with write.locked(cfg, LOCK_SECONDS):
        current = gate.read(cfg, scan.rel).splitlines()
        if any(c.line > len(current) or current[c.line - 1] != c.text for c in scan.candidates):
            row["skipped"].append({"file": scan.rel, "reason": "changed"})
            return
        for v in plan.writes:
            cand = cands[v.line]
            try:
                ev = adapter.first_evidence(scan, cand, v)
                proof = gate.scan_evidence(cfg, ev.text(), "evidence")
                rel, lines, index = fix.locate(
                    cfg, f"{scan.rel}:{v.line}", cand.text, forget=v.verdict == "GONE"
                )
                if v.verdict == "WRONG":
                    lines[index] = core.rebuild_line(cand.text, v.new, ev.repo, ev.sha, day)
                    res = gate.commit(
                        cfg,
                        seg,
                        "fix",
                        rel,
                        writegate.join(lines),
                        proof,
                        run_cap=False,
                        extra=extra,
                    )
                else:
                    res = forget.move(cfg, seg, rel, lines, index, proof, extra, run_cap=False)
            except (Refused, ValueError) as error:
                reject(row, scan.rel, v.line, f"write refused: {error}")
                clean = False
                continue
            if res.id:
                row["writes"].append(res.id)
                row["fixed" if v.verdict == "WRONG" else "archived"] += 1
        if plan.verify and clean and accepted:
            text = gate.read(cfg, scan.rel)
            new = writegate.set_field(text, "verified", day)
            proof = gate.scan_evidence(
                cfg,
                adapter.first_evidence(scan, cands[accepted[0].line], accepted[0]).text(),
                "evidence",
            )
            res = gate.commit(cfg, seg, "verify", scan.rel, new, proof, run_cap=False, extra=extra)
            if res.id:
                row["writes"].append(res.id)
                row["verified"] += 1


def process_file(
    cfg: config.Config,
    seg: Segment | None,
    scan: adapter.FileScan,
    call: Call,
    budget: float,
    row: dict[str, Any],
    room: int,
    notes: list[str],
) -> int:
    try:
        accepted, rejects, cost = ask_model(cfg, scan, call, budget)
    except ModelFailed as error:
        row["cost_usd"] += error.cost
        row["skipped"].append({"file": scan.rel, "reason": f"model: {error}"[:200]})
        raise
    row["cost_usd"] += cost
    row["files_checked"] += 1
    for item in rejects:
        reject(row, scan.rel, item.line, item.reason)
    answered = {v.line for v in accepted if v.verdict != "UNSURE"} | {r.line for r in rejects}
    row["unsure"] += sum(1 for c in scan.candidates if c.line not in answered)
    plan = core.plan_file(
        len(scan.candidates),
        accepted,
        min(cfg.get_int("stale", "max_changes_per_file"), room),
        scan.truncated,
    )
    if plan.reason:
        row["skipped"].append({"file": scan.rel, "reason": plan.reason})
        return 0
    cands = {c.line: c for c in scan.candidates}
    for v in plan.writes:
        notes.append(
            f"{scan.rel}:{v.line} {v.verdict}: {cands[v.line].text}"
            + (f"  ->  {core.tidy_new(cands[v.line].text, v.new)}" if v.verdict == "WRONG" else "")
        )
    if seg is None:
        row["would_fix"] = row.get("would_fix", 0) + sum(
            1 for v in plan.writes if v.verdict == "WRONG"
        )
        row["would_archive"] = row.get("would_archive", 0) + sum(
            1 for v in plan.writes if v.verdict == "GONE"
        )
        return len(plan.writes)
    before = row["fixed"] + row["archived"]
    try:
        commit_changes(cfg, seg, scan, plan, accepted, str(row["run"]), str(row["day"]), row)
    except Refused as error:
        row["skipped"].append({"file": scan.rel, "reason": f"busy: {error}"[:200]})
    return int(row["fixed"] + row["archived"] - before)


def take_snapshot(cfg: config.Config, row: dict[str, Any]) -> None:
    message = f"juno stale-fix {row['day']}: {row['fixed']} fixed, {row['archived']} archived"
    try:
        with write.locked(cfg, write.VIEW_LOCK_SECONDS):
            result = snapshot.take(cfg, message)
        row["snapshot"] = result.get("commit") or result["status"]
    except (snapshot.Failed, Refused) as error:
        row["snapshot"] = f"failed: {error}"[:200]


def execute(
    cfg: config.Config, opts: Options, call: Call = model_call, now: float | None = None
) -> Outcome:
    started = time.monotonic()
    now = time.time() if now is None else now
    out = Outcome(new_row(now, opts))
    row = out.row
    reason = dry_refusal(cfg) if opts.dry else checks.job_refusal(cfg)
    if reason:
        row.update(status="refused", reason=reason)
        stalerun.append_run(cfg, row)
        return out
    if opts.job and not opts.dry:
        if stalerun.week_done(cfg, str(row["week"])):
            row["status"] = "noop"
            return out
        if stalerun.busy(cfg, now):
            row["status"] = "deferred"
            stalerun.append_run(cfg, row)
            return out
    today = datetime.date.fromtimestamp(now)
    files, skipped, probe = adapter.select(
        cfg, cfg.get_int("thresholds", "stale_days"), today, opts.only
    )
    row["skipped"] = skipped
    seg = None if opts.dry else write.guard_job(cfg, stalerow.JOB, str(row["run"]))
    total = cfg.get_float("stale", "run_budget_usd")
    if opts.max_usd is not None:
        total = min(total, opts.max_usd)
    limit = opts.limit if opts.limit is not None else cfg.get_int("stale", "max_files")
    room = cfg.get_int("stale", "max_changes_per_run")
    failures = 0
    for scan in files[:limit]:
        left = total - float(row["cost_usd"])
        if left < cfg.get_float("handoff", "min_run_usd"):
            if not opts.dry:
                row["status"] = "budget"
            break
        if room <= 0:
            row["skipped"].append({"file": scan.rel, "reason": "run cap"})
            continue
        try:
            room -= process_file(
                cfg,
                seg,
                scan,
                call,
                min(cfg.get_float("stale", "file_budget_usd"), left),
                row,
                room,
                out.notes,
            )
            failures = 0
        except ModelFailed:
            failures += 1
            if failures >= MAX_FAILURES:
                row.update(status="failed", reason="the model failed twice in a row")
                break
    for name, pin in probe.pins.items():
        if isinstance(pin, adapter.Pin):
            row["refs"][name] = pin.sha[:8]
            row["fetch"][name] = pin.fetch
        else:
            row["fetch"][name] = pin
    failed = sorted(n for n, p in probe.pins.items() if p == "fetch failed")
    if not files and failed and row["status"] in ("ok", "dry-run"):
        row.update(status="failed", reason="fetch failed for " + ", ".join(failed))
    if row["writes"]:
        take_snapshot(cfg, row)
    row["cost_usd"] = round(float(row["cost_usd"]), 4)
    row["seconds"] = round(time.monotonic() - started, 1)
    stalerun.append_run(cfg, row)
    return out


def add_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--fix",
        action="store_true",
        help="check stale lines against the default branch and fix them",
    )
    parser.add_argument("--dry-run", action="store_true", help="with --fix: write nothing")
    parser.add_argument("--limit", type=int, help="with --fix: files to check")
    parser.add_argument("--max-usd", type=float, help="with --fix: stop when this much is spent")
    parser.add_argument("--file", help="with --fix: only this vault file (knowledge/x.md)")
    parser.add_argument("--job", action="store_true", help="with --fix: the scheduled entry")


def flag_problem(args: argparse.Namespace) -> str | None:
    if not args.fix:
        used = [f for f in ("dry_run", "limit", "max_usd", "file", "job") if getattr(args, f)]
        return f"--{used[0].replace('_', '-')} needs --fix" if used else None
    if args.limit is not None and args.limit < 1:
        return "--limit must be at least 1"
    if args.max_usd is not None and args.max_usd <= 0:
        return "--max-usd must be above 0"
    if args.file and not args.file.startswith(("knowledge/", "projects/")):
        return "--file must be a knowledge/ or projects/ file"
    return None


def render(out: Outcome) -> str:
    row = out.row
    lines = list(out.notes)
    status = row["status"]
    if status in ("refused", "failed"):
        lines.append(f"Juno: stale fixer {status}: {row.get('reason', '')}")
    elif status in ("noop", "deferred"):
        lines.append(f"Juno: stale fixer {status}")
    else:
        verb = "would change" if row.get("dry") else "changed"
        done = (
            f"{row.get('would_fix', 0)} fix, {row.get('would_archive', 0)} archive"
            if row.get("dry")
            else f"{row['fixed']} fixed, {row['archived']} archived, {row['verified']} verified"
        )
        lines.append(
            f"Juno: stale fixer {row['day']} ({status}): {row['files_checked']} files checked, "
            f"{verb} {done}, {row['unsure']} unsure, {len(row['rejected'])} rejected, "
            f"${row['cost_usd']:.2f}"
        )
        if row["writes"]:
            lines.append(f"Juno: revert with `juno undo --run {row['run']}`")
    return "\n".join(lines)


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = config.load(env)
    if not os.path.isdir(cfg.vault):
        sys.stderr.write(f"Juno: vault not found at {cfg.vault}\n")
        return 2
    problem = flag_problem(args)
    if problem:
        sys.stderr.write(f"Juno: {problem}\n")
        return 2
    out = execute(
        cfg,
        Options(args.dry_run, args.job, args.limit, args.max_usd, args.file),
    )
    if args.json:
        print(json.dumps(out.row, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render(out))
    return {"refused": 2, "failed": 1}.get(out.row["status"], 0)
