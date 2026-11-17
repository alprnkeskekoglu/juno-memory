import argparse
import os
import sys
from collections.abc import Mapping
from typing import Any

from juno import config
from juno.adapters import agentcalls, ledgerio, voiceimport, voiceio
from juno.adapters import env as envmod
from juno.cmd import write
from juno.core import voiceimport as core
from juno.core.errors import Refused


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    imp = sub.add_parser("import", help="add your own past messages to the writing voice")
    imp.add_argument("--register", choices=core.REGISTERS, help="register of rows without one")
    imp.add_argument("--target", choices=core.TARGETS, help="target of rows without one")
    imp.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    imp.add_argument("--json", action="store_true", help="print the report as JSON")
    imp.add_argument("file", help="plain text (one message per paragraph) or JSONL; - is stdin")
    forget = sub.add_parser("forget", help="remove an imported batch")
    forget.add_argument("--batch", required=True, help="the batch id `import` printed")
    forget.add_argument("--json", action="store_true", help="print the result as JSON")
    stat = sub.add_parser("status", help="counted samples per register")
    stat.add_argument("--json", action="store_true", help="print the status as JSON")


def guard(cfg: config.Config) -> None:
    """Import and forget write the state file and `self/voice.md`, never the ledger. They refuse
    in a subagent and in a `[nosave]` or private segment; with no session ledger (a plain shell)
    no flag hides anything, so they run."""
    if envmod.agent_context(cfg.env):
        raise Refused("agent context")
    sid = cfg.env.get("CLAUDE_CODE_SESSION_ID", "")
    if sid and agentcalls.active(cfg, sid):
        raise Refused("agent context: a subagent tool call is in flight")
    if sid and ledgerio.exists(cfg, sid):
        seg = ledgerio.fold(cfg, sid)
        if seg.nosave or seg.private:
            raise Refused("[nosave] or private is set in this segment")
    if not voiceio.enabled(cfg):
        raise Refused("[voice] enabled is false")


def read_input(path: str) -> str:
    try:
        if path == "-":
            data = sys.stdin.buffer.read(core.MAX_INPUT_BYTES + 1)
        else:
            with open(os.path.expanduser(path), "rb") as handle:
                data = handle.read(core.MAX_INPUT_BYTES + 1)
    except OSError as error:
        raise Refused(f"cannot read {path!r}: {error.strerror}") from None
    if len(data) > core.MAX_INPUT_BYTES:
        raise Refused("the input is over 20 MB")
    return data.decode("utf-8", "replace")


def skipped_text(counts: Mapping[str, int]) -> str:
    parts = [f"{k} {v}" for k, v in counts.items() if v]
    return f" ({', '.join(parts)})" if parts else ""


def import_text(report: dict[str, Any]) -> str:
    mode = " (dry run)" if report["dry_run"] else ""
    lines = [f"Juno: voice import batch {report['batch']}{mode}"]
    for reg, part in report["registers"].items():
        state = "ready" if part["ready"] else "not ready"
        lines.append(
            f"- {reg}: {part['accepted']} accepted, {part['skipped_total']} skipped"
            f"{skipped_text(part['skipped'])}, {part['counted']} counted, {state}"
        )
    return "\n".join(lines)


def status_text(state: dict[str, Any]) -> str:
    lines = []
    for reg, part in state["registers"].items():
        ready = "ready" if part["ready"] else "not ready"
        lines.append(
            f"{reg}: {part['counted']} counted (user {part['user']}, edited {part['edited']}, "
            f"import {part['import']}) of {part['min_samples']}, {ready}"
        )
    return "\n".join(lines)


def do_import(cfg: config.Config, args: argparse.Namespace) -> int:
    guard(cfg)
    raw = read_input(args.file)
    if args.dry_run:
        report = voiceimport.run_import(cfg, raw, args.register, args.target, True)
    else:
        with write.locked(cfg):
            report = voiceimport.run_import(cfg, raw, args.register, args.target, False)
    write.emit(args, report, import_text(report))
    return 0


def do_forget(cfg: config.Config, args: argparse.Namespace) -> int:
    guard(cfg)
    with write.locked(cfg):
        done = voiceimport.run_forget(cfg, args.batch)
    write.emit(args, done, f"Juno: removed {done['removed']} rows of batch {args.batch}")
    return 0


def do_status(cfg: config.Config, args: argparse.Namespace) -> int:
    state = voiceimport.status(cfg)
    write.emit(args, state, status_text(state))
    return 0


ACTIONS = {"import": do_import, "forget": do_forget, "status": do_status}


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return write.execute(args, env, lambda cfg: ACTIONS[args.action](cfg, args))
