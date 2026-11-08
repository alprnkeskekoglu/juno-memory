import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import gate
from juno.cmd import gateio, write
from juno.core import writegate

REL = "inbox.md"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("text", help="a loose item to sort later")


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    text = gate.scan_line(cfg, args.text, "capture")
    line = f"- {text} · {seg.sid[:8]} · {write.today()}"
    with write.locked(cfg):
        old = gate.read(cfg, REL)
        new, _ = writegate.add_item(old, line, write.preset(cfg), base="# Inbox\n\n")
        res = gate.commit(cfg, seg, "capture", REL, new, f"segment {seg.sid[:8]}")
    return gateio.report(res, "captured in")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
