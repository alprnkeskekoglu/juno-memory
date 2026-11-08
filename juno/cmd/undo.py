import argparse
import os
from collections.abc import Mapping

from juno import config
from juno.adapters import gate, git, writelog
from juno.cmd import gateio, write
from juno.core import writegate
from juno.core.errors import Refused

Record = dict[str, object]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("id", help="a write id printed by a write verb (w-xxxxxxxx)")


def snapshot_hint(cfg: config.Config) -> str:
    vault = os.path.realpath(cfg.vault)
    last = git.run([f"--git-dir={vault}/.git", "log", "-1", "--format=%h %cs"], vault)
    if last:
        return f"the last snapshot is {last} (git -C {vault} show {last.split()[0]}:<file>)"
    return "there is no snapshot yet; run `juno snapshot` after fixing the file by hand"


def refuse(cfg: config.Config, wid: str, why: str) -> Refused:
    return Refused(f"write {wid} cannot be undone cleanly ({why}); {snapshot_hint(cfg)}")


def net_effects(after: list[Record], rel: str) -> list[Record]:
    ids = {r.get("id") for r in after}
    return [
        r
        for r in after
        if r.get("file") == rel
        and r.get("id") not in {x.get("of") for x in after}
        and not (r.get("verb") == "undo" and r.get("of") in ids)
    ]


def plan(cfg: config.Config, records: list[Record], rec: Record) -> tuple[str, str]:
    wid, rel = str(rec["id"]), str(rec["file"])
    mine = writegate.decode(rec.get("hunks"))
    later = [
        writegate.decode(r.get("hunks"))
        for r in net_effects(records[records.index(rec) + 1 :], rel)
    ]
    if writegate.conflicts(mine, bool(rec.get("created")), later):
        raise refuse(cfg, wid, f"a later write changed the same lines of {rel}")
    new = writegate.revert(gate.read(cfg, rel), mine)
    if new is None:
        raise refuse(cfg, wid, f"{rel} no longer holds the written lines")
    return rel, new


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    wid = args.id.strip()
    if not writegate.WRITE_ID_RE.match(wid):
        raise Refused("write id must look like w-1a2b3c4d")
    with write.locked(cfg):
        records = writelog.read(cfg)
        found = next((r for r in records if r.get("id") == wid and r.get("verb") != "undo"), None)
        if found is None:
            raise Refused(f"no write {wid} in the write log")
        if wid in writelog.undone(records):
            raise Refused(f"write {wid} is already undone")
        group = [found, *[r for r in records if r.get("link") == wid]]
        plans = [plan(cfg, records, r) for r in group]
        done = [
            gate.commit(
                cfg,
                seg,
                "undo",
                rel,
                new,
                f"undo {wid}",
                extra={"of": rec["id"]},
                delete=bool(rec.get("created")) and not new,
            )
            for rec, (rel, new) in zip(group, plans, strict=True)
        ]
    files = ", ".join(r.rel for r in done if r.changed)
    print(f"Juno: write {wid} reverted in {files}" if files else f"Juno: write {wid} left nothing")
    return 0


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
