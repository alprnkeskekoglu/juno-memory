import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import gate
from juno.cmd import gateio, write
from juno.core import fold, writegate
from juno.core.errors import Refused

NEW_MAX = 800
WAS_MIN = 8


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("ref", help="<vault-relative file>.md:<line number>")
    parser.add_argument("--was", required=True, help="a fragment of the line as it is now")
    gateio.evidence_option(parser)
    parser.add_argument("text", help="the corrected line")


def locate(cfg: config.Config, ref: str, was: str, forget: bool) -> tuple[str, list[str], int]:
    rel, number = writegate.split_ref(ref)
    writegate.check_target(rel, forget)
    lines = gate.read(cfg, rel).splitlines()
    if number > len(lines):
        raise Refused(f"{rel} has {len(lines)} lines")
    line = lines[number - 1]
    writegate.check_line_fix(line)
    preset = write.preset(cfg)
    fragment = fold.fold(was, preset)
    if len(fragment) < WAS_MIN or fragment not in fold.fold(line, preset):
        raise Refused(f"line {number} of {rel} does not contain --was (the file changed?)")
    return rel, lines, number - 1


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    evidence = gate.anchor(cfg, seg, args.evidence, user_only=False)
    text = gate.scan_text(cfg, args.text, "line")
    if "\n" in text or len(text) > NEW_MAX:
        raise Refused(f"the corrected line must be one line of at most {NEW_MAX} characters")
    with write.locked(cfg):
        rel, lines, index = locate(cfg, args.ref, args.was, forget=False)
        bullet = lines[index].startswith("- ") and not text.startswith("- ")
        lines[index] = ("- " if bullet else "") + text
        res = gate.commit(cfg, seg, "fix", rel, writegate.join(lines), evidence)
    return gateio.report(res, "line corrected in")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
