import argparse
import json
import time

from juno import config
from juno.adapters import checks, gate, taskio, vault
from juno.cmd import backlog, write
from juno.cmd.common import say
from juno.core import taskdigest, writegate
from juno.core.errors import Refused
from juno.core.ledger import Segment
from juno.core.taskrows import Row

MAX_ROWS = 40
MAX_BYTES = 16 * 1024
FIELDS = frozenset({"section", "key", "line"})


def clean_row(cfg: config.Config, section: object, key: object, line: object) -> Row:
    if not (isinstance(section, str) and isinstance(key, str) and isinstance(line, str)):
        raise Refused("section, key and line must be strings")
    taskdigest.check_section(section)
    taskdigest.check_key(key)
    taskdigest.check_text(line)
    return Row(section, key, gate.scan_line(cfg, line, "line", writegate.TASK_LINE_MAX))


def parse_batch(cfg: config.Config, raw: str) -> list[Row]:
    text = backlog.read_body_text(cfg, raw, "--batch-file", MAX_BYTES)
    lines = text.splitlines()
    if not lines:
        raise Refused("--batch-file has no rows")
    if len(lines) > MAX_ROWS:
        raise Refused(f"--batch-file has {len(lines)} rows; at most {MAX_ROWS}")
    rows: list[Row] = []
    for number, line in enumerate(lines, 1):
        try:
            if not line.strip():
                raise Refused("blank line")
            try:
                data = json.loads(line)
            except ValueError:
                raise Refused("not valid JSON") from None
            if not isinstance(data, dict) or set(data) != FIELDS:
                raise Refused('must be an object with exactly "section", "key" and "line"')
            rows.append(clean_row(cfg, data["section"], data["key"], data["line"]))
        except Refused as error:
            raise Refused(f"--batch-file row {number}: {error}") from None
    seen: set[tuple[str, str]] = set()
    for number, row in enumerate(rows, 1):
        pair = (row.section, row.key)
        if pair in seen:
            raise Refused(
                f"--batch-file row {number}: section {row.section!r} key {row.key!r} appears twice"
            )
        seen.add(pair)
    return rows


def rows_from(cfg: config.Config, args: argparse.Namespace) -> list[Row]:
    single = [args.section, args.entry, args.text]
    if args.batch_file:
        if any(v is not None for v in single):
            raise Refused("--batch-file cannot be combined with --section, --key or a line")
        return parse_batch(cfg, args.batch_file)
    if any(v is None for v in single):
        raise Refused("give --section, --key and a line, or --batch-file")
    return [clean_row(cfg, args.section, args.entry, args.text)]


def evidence_for(cfg: config.Config, seg: Segment, key: str, raw: str | None) -> str:
    kind = gate.anchor(cfg, seg, raw, user_only=False)
    if kind.startswith("user ") or kind == f"ticket {key}":
        return kind
    raise Refused(f"evidence must be {taskdigest.ALLOWED.format(key=key)}; got {kind}")


def run_log(cfg: config.Config, key: str, args: argparse.Namespace) -> int:
    try:
        seg = write.guard(cfg)
        if vault.status(cfg.vault) != "juno":
            raise Refused(f"no Juno vault at {cfg.vault}; run juno init")
        rows = rows_from(cfg, args)
        evidence = evidence_for(cfg, seg, key, args.evidence)
        day = time.strftime("%Y-%m-%d")
        rel = taskio.rel(key)
        with write.locked(cfg):
            res, added, replaced = taskio.put_rows(cfg, seg, key, rows, evidence, day)
    except Refused as error:
        say(checks.refusal_line(str(error)))
        return 2
    except OSError as error:
        say(f"Juno: cannot write {taskio.rel(key)}: {error}")
        return 2
    if args.batch_file:
        backlog.consume_body_file(cfg, args.batch_file, "--batch-file")
    if not res.changed:
        say(f"Juno: task {key}: {len(rows)} line(s) unchanged in {rel}; nothing written")
        return 0
    say(
        f"Juno: task {key}: logged {len(rows)} line(s) in {rel} ({added} added, {replaced} "
        f"replaced; write {res.id}; revert with `juno undo {res.id}`)"
    )
    return 0
