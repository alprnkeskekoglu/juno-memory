import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import gate, privacyack, store
from juno.cmd import gateio, write
from juno.core import privacyack as ack_core
from juno.core import writegate
from juno.core.errors import Refused


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    ack = sub.add_parser(
        "ack", help="accept one id finding on a vault line (never secret/phone/email)"
    )
    ack.add_argument("ref", help="<vault-relative file>.md:<line number>")
    ack.add_argument("--was", required=True, help="the digits the doctor flags on that line")
    gateio.evidence_option(ack)
    sub.add_parser("list", help="list the acknowledged id findings")


def do_ack(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    evidence = gate.anchor(cfg, seg, args.evidence, user_only=False)
    with write.locked(cfg):
        rel, number = writegate.split_ref(args.ref)
        lines = gate.read(cfg, rel).splitlines()
        if number > len(lines):
            raise Refused(f"{rel} has {len(lines)} lines")
        span = ack_core.acknowledgeable(lines[number - 1], args.was, gate.prefixes(cfg))
        rec = ack_core.record(rel, span, number, evidence, write.today())
        old = store.read_text(gate.vault_file(cfg, ack_core.ACK_FILE))
        res = gate.commit(
            cfg, seg, "privacy-ack", ack_core.ACK_FILE, ack_core.add(old, rec), evidence
        )
    return gateio.report(res, "id finding acknowledged in")


def do_list(cfg: config.Config, args: argparse.Namespace) -> int:
    records = privacyack.read(cfg)
    if not records:
        print("Juno: no acknowledged id findings")
        return 0
    print(f"Juno: {ack_core.count_line(records)}")
    for rec in records:
        text = gate.read(cfg, str(rec["file"]))
        line = ack_core.resolve(text, rec, gate.prefixes(cfg)) if text else None
        where = f"{rec['file']}:{line}" if line else f"{rec['file']} (line gone)"
        print(f"- {where} · {rec.get('t', '?')} · {rec.get('evidence', '')}")
    return 0


ACTIONS = {"ack": do_ack, "list": do_list}


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return write.execute(args, env, lambda cfg: ACTIONS[args.action](cfg, args))
