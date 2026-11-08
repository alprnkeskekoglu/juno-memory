import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import gate
from juno.cmd import fix, gateio, write
from juno.core import writegate

ARCHIVE = "archive/decayed/forgotten-{month}.md"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("ref", help="<vault-relative file>.md:<line number>")
    parser.add_argument("--was", required=True, help="a fragment of the line as it is now")
    gateio.evidence_option(parser, "why the line is wrong: your words, or repo:<name>/ticket:<KEY>")


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    evidence = gate.anchor(cfg, seg, args.evidence, user_only=False)
    day = write.today()
    archive = ARCHIVE.format(month=day[:7])
    with write.locked(cfg):
        rel, lines, index = fix.locate(cfg, args.ref, args.was, forget=True)
        gone = lines.pop(index)
        res = gate.commit(cfg, seg, "forget", rel, writegate.join(lines), evidence)
        kept = f"{gone} · forgotten {day} · from {rel} · {res.id}"
        text = gate.read(cfg, archive) or "# Forgotten lines\n\n"
        new = writegate.join([*text.splitlines(), kept])
        gate.commit(cfg, seg, "forget-archive", archive, new, evidence, extra={"link": res.id})
    return gateio.report(res, "line moved to the archive from")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
