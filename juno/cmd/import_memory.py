import argparse
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

from juno import config
from juno.adapters import cardjob, gate, ledgerio, nativescan, queues
from juno.adapters import env as envmod
from juno.cmd import gateio, import_settle, project, write
from juno.core import candidates, fold, nativememory, selfguard, selfmodel, writegate
from juno.core.errors import Refused
from juno.core.ledger import Segment

JOB = "memory-import"


@dataclass
class Report:
    run: str
    written: list[str] = field(default_factory=list)
    unchanged: int = 0
    queued: int = 0
    queued_self: int = 0
    over: int = 0
    known: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run", action="store_true", help="show what would be imported, write nothing"
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("accept", "reject"),
        help="settle the queued candidates, rules and self lines together (no ids: the batch)",
    )
    parser.add_argument("ids", nargs="*", metavar="ID", help="candidate ids (review path)")


def refusal(cfg: config.Config) -> str | None:
    if cfg.env.get("JUNO_DISABLE") == "1":
        return "JUNO_DISABLE is set"
    if any(cfg.env.get(name) for name in cardjob.SESSION_VARS):
        return "run it in a terminal outside Claude Code"
    if envmod.agent_context(cfg.env):
        return "agent context"
    return None


def fact_line(cfg: config.Config, item: nativescan.Found, limit: int) -> str:
    return gate.scan_line(cfg, nativememory.fact(item.note, limit), "memory line")


def put_vault(
    cfg: config.Config, seg: Segment, item: nativescan.Found, extra: dict[str, object]
) -> bool:
    text = fact_line(cfg, item, nativememory.TEXT_MAX)
    proof = gate.import_evidence(item.source)
    day = write.today()
    if item.repo:
        rel, section = f"projects/{item.repo}.md", "Gotchas"
        base = writegate.frontmatter({"repo": item.repo, "updated": day}) + project.SKELETON
        line = f"- {text} · {day}"
    else:
        topic = nativememory.topic(item.note)
        rel, section = f"knowledge/{topic}.md", None
        head = writegate.frontmatter(
            {"topic": topic, "sources": f"[{proof}]", "valid_from": day, "verified": day}
        )
        base = f"{head}\n# {topic}\n\n"
        line = f"- {text} · src: {proof} · {day}"
    with write.locked(cfg):
        old = gate.read(cfg, rel)
        new, _ = writegate.add_item(old, line, write.preset(cfg), section=section, base=base)
        if item.repo and new != old:
            new = writegate.set_field(new, "updated", day)
        res = gate.commit(cfg, seg, "import", rel, new, proof, run_cap=False, extra=extra)
    return res.changed


def rule_item(cfg: config.Config, item: nativescan.Found) -> queues.Item:
    limit = min(cfg.get_int("caps", "rule_max_chars"), nativememory.TEXT_MAX)
    text = fact_line(cfg, item, limit)
    preset = write.preset(cfg)
    return {
        "id": candidates.digest_id("r", "import", item.source, fold.fold(text, preset)),
        "scope": nativememory.rule_scope(item.repo),
        "rule": text,
        "quote": "",
        "source": item.source,
        "batch": nativememory.BATCH,
        "kind": item.note.type,
        "t": time.time(),
        "status": "pending",
    }


def self_item(cfg: config.Config, item: nativescan.Found) -> queues.Item:
    """A user note becomes a self candidate: nothing reaches self/ before it is accepted."""
    text = fact_line(cfg, item, min(selfmodel.TEXT_CHARS[1], nativememory.TEXT_MAX))
    problem = selfguard.content_problem(text, text)
    if problem:
        raise Refused(f"user note refused ({problem}); not queued")
    return {
        "id": candidates.digest_id("s", "import", item.source, fold.fold(text, write.preset(cfg))),
        "target": "self",
        "file": nativememory.self_file(item.note),
        "text": text,
        "source": item.source,
        "batch": nativememory.BATCH,
        "kind": item.note.type,
        "t": time.time(),
        "status": "pending",
    }


def queue_all(
    cfg: config.Config, rules: list[queues.Item], mine: list[queues.Item], report: Report
) -> None:
    """Queue the batch; it has its own budget (a hard limit on pending import candidates)."""
    room = nativememory.BATCH_MAX - sum(
        1
        for queue in (queues.RULES, queues.SELF)
        for i in queues.pending(cfg, queue)
        if candidates.in_import_batch(i)
    )

    def add_to(queue: queues.Queue, built: list[queues.Item]) -> tuple[int, int]:
        left = [room]

        def add(items: list[queues.Item]) -> tuple[int, int]:
            items[:] = candidates.prune(items, time.time())
            have = {str(i["id"]) for i in items}
            queued = 0
            for item in built:
                if item["id"] in have:
                    report.known += 1
                elif left[0] <= 0:
                    report.over += 1
                else:
                    items.append(item)
                    have.add(str(item["id"]))
                    left[0] -= 1
                    queued += 1
            return queued, left[0]

        return queues.update(cfg, queue, add)

    report.queued, room = add_to(queues.RULES, rules)
    report.queued_self, room = add_to(queues.SELF, mine)


def run_import(cfg: config.Config, found: list[nativescan.Found], dry: bool) -> Report:
    report = Report("imp-" + uuid.uuid4().hex[:8])
    seg = None if dry else ledgerio.fold(cfg, f"job-{JOB}-{report.run}")
    extra: dict[str, object] = {"actor": f"job:{JOB}", "run": report.run}
    built: list[queues.Item] = []
    mine: list[queues.Item] = []
    for item in found:
        where = f"{item.slug}/{item.note.file}"
        kind = nativememory.target(item.note, item.repo)
        try:
            if kind == "rule":
                built.append(rule_item(cfg, item))
            elif kind == "user":
                mine.append(self_item(cfg, item))
            elif dry:
                fact_line(cfg, item, nativememory.TEXT_MAX)
                report.written.append(where)
            elif seg is not None and put_vault(cfg, seg, item, dict(extra)):
                report.written.append(where)
            else:
                report.unchanged += 1
        except Refused as error:
            report.skipped.append((where, str(error)))
    if dry:
        report.queued, report.queued_self = len(built), len(mine)
    elif built or mine:
        queue_all(cfg, built, mine, report)
    return report


def describe(report: Report, dry: bool) -> list[str]:
    verb = "would be" if dry else "were"
    lines = [
        f"Juno: memory import: {len(report.written)} notes {verb} written to the vault, "
        f"{report.queued} {verb} queued as rule candidates and "
        f"{report.queued_self} as self candidates"
        + (f", {report.known} already queued" if report.known else "")
        + (f", {report.unchanged} already in the vault" if report.unchanged else "")
    ]
    if report.over:
        lines.append(
            f"Juno: {report.over} over the limit of {nativememory.BATCH_MAX} pending import "
            "candidates were not queued; settle the pending ones, then run `juno import-memory` "
            "again to continue"
        )
    lines += [f"Juno: skipped {where}: {why}" for where, why in report.skipped]
    if dry:
        return lines
    if report.written:
        lines.append(f"Juno: undo the vault writes with `juno undo --run {report.run}`")
    if report.queued or report.queued_self:
        lines.append(
            "Juno: candidates wait for your approval at the next session start "
            "(`juno rule pending`; `juno import-memory accept` or `reject` settles all of them)"
        )
    lines.append(
        "Juno: no write cap applies to the import; running it again adds nothing twice; "
        "undecided import candidates do not expire"
    )
    return lines


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    reason = refusal(cfg)
    if reason:
        raise Refused(reason)
    if args.ids:
        raise Refused("ids need accept or reject")
    found = nativescan.scan(cfg)
    if not found:
        print("Juno: no importable Claude memory notes found")
        return 0
    print(f"Juno: Claude's own memory has {nativescan.summary(found)}")
    for line in describe(run_import(cfg, found, bool(args.dry_run)), bool(args.dry_run)):
        print(line)
    return 0


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    if args.action:
        return gateio.run_verb(args, env, import_settle.do)
    if args.ids:
        raise SystemExit("juno import-memory: ids need accept or reject")
    return gateio.run_verb(args, env, do)
