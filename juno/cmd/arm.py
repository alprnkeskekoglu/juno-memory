import argparse
import json
import os
import secrets
import time
from collections.abc import Mapping
from typing import Any

from juno import config
from juno.adapters import armseg, armsio, asklog, cardjob, evalio, fs, shadowio
from juno.cmd import backlog, write
from juno.cmd.write import Refused
from juno.core import armmetrics, arms, fold

LOG_BYTES = 1 << 20


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    start = sub.add_parser("start", help="start a pre-registered arm (max 2 running)")
    start.add_argument("name")
    start.add_argument("--prereg", required=True, metavar="FILE", help="JSON under /tmp or state")
    status = sub.add_parser("status", help="list the arms with their live counts")
    status.add_argument("--json", action="store_true")
    stop = sub.add_parser("stop", help="stop a running arm without a decision")
    stop.add_argument("name")
    stop.add_argument("--reason", required=True)
    decide = sub.add_parser("decide", help="run the pre-registered metric and close the arm")
    decide.add_argument("name")
    decide.add_argument("--json", action="store_true")


def check_name(name: str) -> None:
    if not arms.NAME_RE.match(name):
        raise Refused(f"{name!r} is not an arm name (^[a-z][a-z0-9-]{{1,30}}$)")


def load_arm(cfg: config.Config, name: str) -> dict[str, Any]:
    check_name(name)
    data = armsio.read_arm(armsio.arm_path(cfg, name))
    if data is None:
        raise Refused(f"no arm named {name}")
    return data


def log(cfg: config.Config, event: str, name: str, **extra: Any) -> None:
    row = {"t": int(time.time()), "ev": event, "name": name, **extra}
    path = os.path.join(armsio.arms_dir(cfg), armsio.LOG)
    fs.append_log(path, json.dumps(row, ensure_ascii=False, sort_keys=True), LOG_BYTES)


def save(cfg: config.Config, data: dict[str, Any]) -> None:
    raw = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fs.atomic_write(armsio.arm_path(cfg, data["name"]), raw.encode("utf-8"), 0o600)


def running_names(cfg: config.Config) -> list[str]:
    found = []
    for name in armsio.names(cfg):
        data = armsio.read_arm(armsio.arm_path(cfg, name))
        if data and data.get("status") == "running":
            found.append(name)
    return found


def measure(cfg: config.Config, name: str) -> dict[str, Any]:
    data = load_arm(cfg, name)
    code = data["prereg"]["metric"]["code"]
    entries, _ = shadowio.read(cfg.state)
    cues = cfg.language_list("correction_cues")
    preset = cfg.get_str("language", "preset")
    inputs: dict[str, Any] = {
        "shadow": entries,
        "has_cue": lambda text: fold.has_cue(text, cues, preset),
    }
    if code in ("linked_read_share", "next_lookup_calls"):
        inputs.update(linked_inputs(cfg, name))
    if code in ("recommended_pick", "pushback_rate"):
        inputs.update(self_inputs(cfg))
    return armmetrics.METRICS[code](name, inputs)


def linked_inputs(cfg: config.Config, name: str) -> dict[str, Any]:
    armseg.sweep(cfg, force=True)
    rows = armsio.read_segments(cfg)
    packs, sides = evalio.ledger_arm_data(cfg, skip=rows)
    return {
        "rows": rows,
        "packs": lambda: packs,
        "side_of": lambda sid: sides.get(sid, {}).get(name),
        "reads": lambda sid: armseg.reads_of(cfg, sid),
    }


def self_inputs(cfg: config.Config) -> dict[str, Any]:
    armseg.sweep(cfg, force=True)
    return {
        "rows": armsio.read_segments(cfg),
        "asks": lambda: asklog.read(cfg.state),
        "runs": lambda: cardjob.read_runs(cfg),
    }


def do_start(cfg: config.Config, args: argparse.Namespace) -> int:
    write.guard(cfg)
    check_name(args.name)
    try:
        prereg = json.loads(backlog.read_body_text(cfg, args.prereg))
    except ValueError as error:
        raise Refused(f"--prereg is not valid JSON: {error}") from None
    problems = arms.validate(prereg, armmetrics.METRICS)
    if problems:
        raise Refused("pre-registration refused: " + "; ".join(problems))
    fs.ensure_private_dir(armsio.arms_dir(cfg))
    with fs.locked(os.path.join(armsio.arms_dir(cfg), "arms.lock")):
        if os.path.exists(armsio.arm_path(cfg, args.name)):
            raise Refused(f"arm {args.name} already exists; names are never reused")
        live = running_names(cfg)
        if len(live) >= arms.MAX_RUNNING:
            raise Refused(f"{len(live)} arms already running ({', '.join(live)}); the limit is 2")
        data = {
            "name": args.name,
            "prereg": prereg,
            "salt": secrets.token_hex(8),
            "started": int(time.time()),
            "sha256": arms.body_hash(prereg),
            "status": "running",
            "result": None,
        }
        save(cfg, data)
        log(cfg, "start", args.name, sha256=data["sha256"])
    print(f"Juno: arm {args.name} started at fraction {prereg['assignment']['fraction']}")
    return 0


def do_stop(cfg: config.Config, args: argparse.Namespace) -> int:
    write.guard(cfg)
    reason = write.clean_line(cfg, fold.nfc(args.reason), "reason")
    with fs.locked(os.path.join(armsio.arms_dir(cfg), "arms.lock")):
        data = load_arm(cfg, args.name)
        if data.get("status") != "running":
            raise Refused(f"arm {args.name} is {data.get('status')}, not running")
        data.update(status="stopped", stopped=int(time.time()), reason=reason)
        save(cfg, data)
        log(cfg, "stop", args.name, reason=reason)
    print(f"Juno: arm {args.name} stopped")
    return 0


def do_decide(cfg: config.Config, args: argparse.Namespace) -> int:
    with fs.locked(os.path.join(armsio.arms_dir(cfg), "arms.lock")):
        data = load_arm(cfg, args.name)
        if data.get("status") != "running":
            raise Refused(f"arm {args.name} is {data.get('status')}, not running")
        prereg = data["prereg"]
        if arms.body_hash(prereg) != data.get("sha256"):
            raise Refused(f"arm {args.name}: the pre-registration changed after start (sha256)")
        outcome = measure(cfg, args.name)
        now = time.time()
        reason = arms.decide_refusal(prereg, data["started"], now, outcome)
        if reason:
            raise Refused(f"arm {args.name}: {reason}")
        data.update(status="decided", result={"decided": int(now), **outcome})
        save(cfg, data)
        log(cfg, "decide", args.name)
    write.emit(args, data["result"], json.dumps(data["result"], ensure_ascii=False, sort_keys=True))
    return 0


def describe(cfg: config.Config, name: str) -> dict[str, Any]:
    data = load_arm(cfg, name)
    prereg = data["prereg"]
    row: dict[str, Any] = {
        "name": name,
        "status": data.get("status"),
        "fraction": prereg["assignment"]["fraction"],
        "min_n": prereg["min_n"],
        "max_days": prereg["max_days"],
        "started": data.get("started"),
        "result": data.get("result"),
    }
    if data.get("status") == "running":
        try:
            row["live"] = measure(cfg, name)
        except KeyError, OSError, ValueError:
            row["live"] = None
    return row


def status_report(cfg: config.Config, as_json: bool) -> int:
    rows = [describe(cfg, n) for n in armsio.names(cfg)]
    if as_json:
        print(json.dumps({"arms": rows}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if not rows:
        print("no arms")
    for r in rows:
        live = r.get("live") or r.get("result") or {}
        n = live.get("n", {})
        day = time.strftime("%Y-%m-%d", time.localtime(r["started"])) if r["started"] else "-"
        print(
            f"{r['name']}: {r['status']}, fraction {r['fraction']}, since {day}, "
            f"segments on {n.get('on', '-')} / off {n.get('off', '-')}, "
            f"min_n {r['min_n']}, max_days {r['max_days']}"
        )
    return 0


def do_status(cfg: config.Config, args: argparse.Namespace) -> int:
    return status_report(cfg, args.json)


ACTIONS = {"start": do_start, "status": do_status, "stop": do_stop, "decide": do_decide}


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return write.execute(args, env, lambda cfg: ACTIONS[args.action](cfg, args))
