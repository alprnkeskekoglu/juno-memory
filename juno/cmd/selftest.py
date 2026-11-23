import argparse
import json
import os
import time
from collections.abc import Mapping
from typing import Any

from juno import config
from juno.adapters import agentcalls, gate, ledgerio, selftestio
from juno.adapters import env as envmod
from juno.cmd import write
from juno.core import selftest as core
from juno.core.errors import Refused


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    add = sub.add_parser("add", help="save a question and the vault file that should answer it")
    add.add_argument("question")
    where = add.add_mutually_exclusive_group(required=True)
    where.add_argument("--expect", metavar="PATH", help="vault-relative path of the right note")
    where.add_argument(
        "--control", action="store_true", help="a question the search should answer with nothing"
    )
    add.add_argument("--sentence", help="a phrase the returned text should contain")
    add.add_argument("--json", action="store_true")
    lst = sub.add_parser("list", help="show the saved questions")
    lst.add_argument("--json", action="store_true")
    rem = sub.add_parser("remove", help="delete a saved question")
    rem.add_argument("id")
    rem.add_argument("--json", action="store_true")
    run = sub.add_parser("run", help="ask every question the way `juno search` does and score it")
    run.add_argument("--limit", type=int, default=core.DEFAULT_LIMIT)
    run.add_argument("--budget", type=int, metavar="CHARS", help="cut the returned text here")
    run.add_argument("--json", action="store_true")


def guard(cfg: config.Config) -> None:
    """`add` and `remove` write the state file. They refuse in a subagent and in a `[nosave]` or
    private segment; with no session ledger (a plain shell) no flag hides anything, so they run."""
    if envmod.agent_context(cfg.env):
        raise Refused("agent context")
    sid = cfg.env.get("CLAUDE_CODE_SESSION_ID", "")
    if sid and agentcalls.active(cfg, sid):
        raise Refused("agent context: a subagent tool call is in flight")
    if sid and ledgerio.exists(cfg, sid):
        seg = ledgerio.fold(cfg, sid)
        if seg.nosave or seg.private:
            raise Refused("[nosave] or private is set in this segment")


def emit(args: argparse.Namespace, data: Any, text: str) -> None:
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(text)


def describe(row: Mapping[str, Any]) -> str:
    target = "control (expects no result)" if row["control"] else f"-> {row['expect']}"
    extra = f' [sentence: "{row["sentence"]}"]' if row.get("sentence") else ""
    return f"{row['id']}  {row['question']}  {target}{extra}"


def add(cfg: config.Config, args: argparse.Namespace) -> int:
    guard(cfg)
    try:
        question = core.check_text(args.question, "question", core.MAX_QUESTION)
        sentence = (
            core.check_text(args.sentence, "--sentence", core.MAX_SENTENCE)
            if args.sentence is not None
            else None
        )
        expect = None
        if args.control:
            if sentence:
                raise core.Invalid("--sentence does not go with --control")
        else:
            expect = core.expect_syntax(args.expect, cfg.vault)
            selftestio.vault_file(cfg, expect)
    except core.Invalid as error:
        raise Refused(str(error)) from None
    question = gate.scan_text(cfg, question, "question")
    if sentence:
        sentence = gate.scan_text(cfg, sentence, "--sentence")
    try:
        with selftestio.locked(cfg):
            state = selftestio.load(cfg)
            if len(state["questions"]) >= core.MAX_QUESTIONS:
                raise Refused(f"already {core.MAX_QUESTIONS} questions; remove some first")
            when = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
            row = core.new_row(f"q{state['next']}", question, expect, sentence, when)
            state["next"] += 1
            state["questions"].append(row)
            selftestio.save(cfg, state)
    except TimeoutError:
        raise Refused("the selftest file is busy; retry in a moment") from None
    emit(args, row, "added " + describe(row))
    return 0


def list_(cfg: config.Config, args: argparse.Namespace) -> int:
    rows = selftestio.load(cfg)["questions"]
    emit(args, {"questions": rows}, "\n".join(describe(r) for r in rows) or "no questions")
    return 0


def remove(cfg: config.Config, args: argparse.Namespace) -> int:
    guard(cfg)
    try:
        with selftestio.locked(cfg):
            state = selftestio.load(cfg)
            keep = [r for r in state["questions"] if r["id"] != args.id]
            if len(keep) == len(state["questions"]):
                raise Refused(f"no question with id {args.id}")
            state["questions"] = keep
            selftestio.save(cfg, state)
    except TimeoutError:
        raise Refused("the selftest file is busy; retry in a moment") from None
    emit(args, {"removed": args.id}, f"removed {args.id}")
    return 0


def mark(res: Mapping[str, Any]) -> str:
    if res["control"]:
        return "PASS" if res["passed"] else "FAIL"
    return f"#{res['rank']}" if res["rank"] else "miss"


def run_text(report: Mapping[str, Any]) -> str:
    lines = []
    for res in report["results"]:
        extra = f"  sentence: {res['sentence']}" if res["sentence"] else ""
        kind = " (control)" if res["control"] else ""
        lines.append(f"{res['id']:<5} {mark(res):<5} {res['question']}{kind}{extra}")
    s = report["summary"]
    if s["questions"]:
        lines.append(
            f"hit@1 {s['hit_at_1']}/{s['questions']}  hit@{report['limit']} "
            f"{s['hit_at_k']}/{s['questions']}  "
            f"(Wilson lower bound {s['wilson_lower_hit_at_k']:.2f})"
        )
    if s["sentences"]:
        lines.append(
            f"sentences: found {s['sentence_found']}, cut by budget {s['sentence_cut_by_budget']}, "
            f"absent {s['sentence_absent']}"
        )
    if s["controls"]:
        lines.append(f"controls: passed {s['controls_passed']}/{s['controls']}")
    return "\n".join(lines) or "no questions"


def run_all(cfg: config.Config, args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise Refused("--limit must be at least 1")
    if args.budget is not None and args.budget < 0:
        raise Refused("--budget must be 0 or more")
    if not os.path.isdir(cfg.vault):
        raise Refused(f"vault not found at {cfg.vault}")
    rows = selftestio.load(cfg)["questions"]
    results: list[dict[str, Any]] = []
    if rows:
        try:
            db = selftestio.synced_db(cfg)
        except TimeoutError:
            raise Refused("the index is busy; retry in a moment") from None
        for row in rows:
            hits = selftestio.search(cfg, db, row["question"], args.limit)
            results.append(core.score(row, hits, args.budget))
    report = {
        "limit": args.limit,
        "budget": args.budget,
        "results": results,
        "summary": core.summarize(results),
    }
    emit(args, report, run_text(report))
    return 0


ACTIONS = {"add": add, "list": list_, "remove": remove, "run": run_all}


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return write.execute(args, env, lambda cfg: ACTIONS[args.action](cfg, args))
