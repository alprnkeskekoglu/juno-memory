import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import gate
from juno.cmd import fix, gateio, write
from juno.core import writegate
from juno.core.ledger import Segment

ARCHIVE = "archive/decayed/forgotten-{month}.md"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("ref", help="<vault-relative file>.md:<line number>")
    parser.add_argument("--was", required=True, help="a fragment of the line as it is now")
    gateio.evidence_option(parser, "why the line is wrong: your words, or repo:<name>/ticket:<KEY>")


def move(
    cfg: config.Config,
    seg: Segment,
    rel: str,
    lines: list[str],
    index: int,
    evidence: str,
    extra: dict[str, object] | None = None,
    run_cap: bool = True,
) -> gate.Result:
    day = write.today()
    archive = ARCHIVE.format(month=day[:7])
    gone = lines.pop(index)
    res = gate.commit(
        cfg, seg, "forget", rel, writegate.join(lines), evidence, extra=extra, run_cap=run_cap
    )
    kept = f"{gone} · forgotten {day} · from {rel} · {res.id}"
    text = gate.read(cfg, archive) or "# Forgotten lines\n\n"
    new = writegate.join([*text.splitlines(), kept])
    gate.commit(
        cfg,
        seg,
        "forget-archive",
        archive,
        new,
        evidence,
        extra={**(extra or {}), "link": res.id},
        run_cap=run_cap,
    )
    return res


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    evidence = gate.anchor(cfg, seg, args.evidence, user_only=False)
    with write.locked(cfg):
        rel, lines, index = fix.locate(cfg, args.ref, args.was, forget=True)
        res = move(cfg, seg, rel, lines, index, evidence)
    return gateio.report(res, "line moved to the archive from")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
