import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from typing import Any

from juno import config, paths
from juno.adapters import evalio, index, store
from juno.cmd import arm
from juno.core import eval as core


class Refused(Exception):
    pass


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    sample = sub.add_parser("sample", help="write a labelling set from the shadow log")
    sample.add_argument("--n", type=int, default=30)
    sample.add_argument("--exclude-set", metavar="PATH")
    sample.add_argument("--since", metavar="YYYY-MM-DD", help="only entries from this local day on")
    freeze = sub.add_parser("freeze", help="write the sha256 sidecar of a labelled set")
    freeze.add_argument("set")
    floor = sub.add_parser("floor", help="choose the score floor on a frozen set")
    floor.add_argument("set")
    gate = sub.add_parser("gate", help="check the plan thresholds on a held-out frozen set")
    gate.add_argument("set")
    gate.add_argument("--floor", type=float, required=True)
    recall = sub.add_parser("recall", help="offline recall proxy over the shadow log")
    recall.add_argument("--floor", type=float, default=0.0)
    agree = sub.add_parser("agree", help="label agreement against the second labeller")
    agree.add_argument("set")
    compare = sub.add_parser("compare", help="paired offline comparison of a retrieval setting")
    compare.add_argument("--setting", choices=["keys"], required=True)
    compare.add_argument("--weights", default="0,1,2,4", help="comma list of keys weights")
    sub.add_parser("ab", help="alias of juno arm status")
    for action in sub.choices.values():
        action.add_argument("--json", action="store_true")


def emit(args: argparse.Namespace, data: Any, text: str) -> None:
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        print(text)


def transcripts_dir(cfg: config.Config) -> str:
    projects = cfg.env.get("JUNO_CLAUDE_PROJECTS") or os.path.join(cfg.claude_dir, "projects")
    return paths.expand(projects, cfg.env)


def synced_db(cfg: config.Config) -> str:
    if not os.path.isdir(cfg.vault):
        raise Refused(f"vault not found at {cfg.vault}")
    return index.sync(cfg)[0]


def sample(args: argparse.Namespace, cfg: config.Config) -> int:
    if args.n < 1:
        raise Refused("--n must be at least 1")
    exclude: list[str] = []
    if args.exclude_set:
        exclude = [e["id"] for e in evalio.load_raw(args.exclude_set)["entries"]]
    since = None
    if args.since:
        try:
            since = time.mktime(time.strptime(args.since, "%Y-%m-%d"))
        except ValueError:
            raise Refused("--since must be YYYY-MM-DD") from None
    picked = evalio.sample_for_labelling(cfg.state, n=args.n, exclude_ids=exclude, since=since)
    emit(args, {"path": picked.path, "entries": len(picked)}, picked.path)
    return 0


def freeze(args: argparse.Namespace, cfg: config.Config) -> int:
    target = os.path.abspath(args.set)
    if store.in_vault(cfg, target) or store.in_vault(cfg, os.path.dirname(target)):
        raise Refused("the eval set must not be inside the vault")
    digest = evalio.freeze_set(args.set)
    emit(args, {"set": args.set, "sha256": digest}, f"frozen {args.set} sha256 {digest}")
    return 0


def floor(args: argparse.Namespace, cfg: config.Config) -> int:
    value = evalio.choose_floor(args.set, synced_db(cfg), cfg.get_str("language", "preset"))
    emit(args, {"floor": value}, str(value))
    return 0


def gate(args: argparse.Namespace, cfg: config.Config) -> int:
    preset = cfg.get_str("language", "preset")
    result = evalio.gate(args.set, synced_db(cfg), args.floor, preset)
    evalio.write_last_gate(cfg.state, result, args.set, args.floor)
    lines = [result["report"].rstrip("\n"), "gate: " + ("PASS" if result["passed"] else "FAIL")]
    lines += ["- " + reason for reason in result["reasons"]]
    emit(args, result, "\n".join(lines))
    return 0 if result["passed"] else 1


def recall(args: argparse.Namespace, cfg: config.Config) -> int:
    result = evalio.recall_proxy(cfg.state, cfg.vault, transcripts_dir(cfg), floor=args.floor)
    emit(args, result, core.recall_report(result).rstrip("\n"))
    return 0


def agree(args: argparse.Namespace, cfg: config.Config) -> int:
    result = evalio.agreement(args.set)
    emit(args, result, core.agreement_report(result).rstrip("\n"))
    return 0


def compare(args: argparse.Namespace, cfg: config.Config) -> int:
    try:
        weights = [float(w) for w in args.weights.split(",") if w.strip()]
    except ValueError:
        raise Refused("--weights must be a comma list of numbers") from None
    if not weights or any(w < 0 for w in weights):
        raise Refused("--weights must be non-negative numbers")
    result = evalio.compare_keys(cfg, transcripts_dir(cfg), synced_db(cfg), weights)
    emit(args, result, core.compare_report(result).rstrip("\n"))
    return 0


def ab(args: argparse.Namespace, cfg: config.Config) -> int:
    return arm.status_report(cfg, args.json)


ACTIONS: dict[str, Callable[[argparse.Namespace, config.Config], int]] = {
    "sample": sample,
    "freeze": freeze,
    "floor": floor,
    "gate": gate,
    "recall": recall,
    "agree": agree,
    "compare": compare,
    "ab": ab,
}


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = config.load(env)
    try:
        return ACTIONS[args.action](args, cfg)
    except (Refused, OSError, ValueError, KeyError, TypeError) as error:
        sys.stderr.write(f"Juno: refused: {type(error).__name__}: {error}\n")
        return 2
