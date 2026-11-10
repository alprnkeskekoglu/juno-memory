import argparse
import os
import re
import time
from collections.abc import Mapping

from juno import config
from juno.adapters import fs, gate, ledgerio, queues, store
from juno.cmd import fix, gateio, write
from juno.cmd.write import Refused
from juno.core import fold, threadscan, writegate
from juno.core.vaultfmt import THREAD_RE

TEXT_CHARS = (10, 160)
MAX_LINES = 30
MAX_PER_TAG = 5
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    add = sub.add_parser("add", help="open a thread in threads.md")
    add.add_argument("--tag", action="append", required=True, help="repo or ticket (repeatable)")
    add.add_argument("text")
    lst = sub.add_parser("list", help="print the open threads")
    lst.add_argument("--json", action="store_true")
    lst.add_argument("--stale", action="store_true", help="only threads older than --days")
    lst.add_argument("--days", type=int, default=threadscan.STALE_DAYS, help="age for --stale")
    pend = sub.add_parser("pending", help="list pending thread close candidates")
    pend.add_argument("--json", action="store_true")
    close = sub.add_parser(
        "close",
        help="close thread candidates by id, or one thread line directly "
        "(--line N --was TEXT --evidence YOUR_WORDS)",
    )
    close.add_argument("ids", nargs="*", metavar="ID", help="candidate ids from `thread pending`")
    close.add_argument("--line", type=int, help="line number in threads.md (direct form)")
    close.add_argument("--was", help="part of that line as it is now (direct form)")
    close.add_argument(
        "--evidence",
        help="your own words, typed or chosen this segment, 20+ characters, verbatim "
        "(direct form; repo:/ticket: anchors are not accepted)",
    )
    keep = sub.add_parser("keep", help="keep thread candidates")
    keep.add_argument("ids", nargs="+", metavar="ID")


def threads_path(cfg: config.Config) -> str:
    path = os.path.join(cfg.vault, "threads.md")
    if os.path.islink(path):
        raise Refused("threads.md is a symlink")
    return path


def thread_lines(text: str) -> list[str]:
    return [line.rstrip() for line in text.splitlines() if THREAD_RE.match(line.rstrip())]


def tags_of(line: str) -> list[str]:
    match = THREAD_RE.match(line)
    return [t.strip().casefold() for t in match.group(1).split(",")] if match else []


def do_add(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    tags: list[str] = []
    for raw in args.tag:
        tag = raw.strip()
        if not TAG_RE.match(tag):
            raise Refused(f"{raw!r} is not a thread tag (a repo or ticket name)")
        if tag not in tags:
            tags.append(tag)
    text = write.clean_line(cfg, fold.nfc(args.text), "thread text")
    low, high = TEXT_CHARS
    if not low <= len(text) <= high:
        raise Refused(f"thread text must be {low}-{high} characters")
    if "[" in text:
        raise Refused("thread text must not contain '['")
    path = threads_path(cfg)
    line = f"- [{', '.join(tags)}] {text} · since {write.today()}"
    with write.locked(cfg):
        current = store.read_text(path)
        lines = current.splitlines()
        open_lines = [x for x in lines if x.startswith("- ")]
        preset = write.preset(cfg)
        if fold.fold(text, preset) in fold.fold("\n".join(open_lines), preset):
            print("Juno: thread already open")
            return 0
        near = threadscan.near_index(lines, tags, text, preset)
        if near is not None:
            line = threadscan.reword(lines[near], text)
            if line == lines[near].rstrip():
                print("Juno: thread already open")
                return 0
            lines[near] = line
            gate.commit(
                cfg, seg, "thread", "threads.md", writegate.join(lines), f"segment {seg.sid[:8]}"
            )
            print(f"Juno: near-duplicate thread updated in threads.md:\n{line}")
            return 0
        if len(open_lines) >= MAX_LINES:
            raise Refused(
                f"threads.md already has {len(open_lines)} open threads (cap {MAX_LINES})"
            )
        for tag in tags:
            if sum(1 for x in open_lines if tag.casefold() in tags_of(x)) >= MAX_PER_TAG:
                raise Refused(f"{tag} already has {MAX_PER_TAG} open threads; close one first")
        body = current if current.strip() else "# Threads\n\n"
        text_out = body.rstrip("\n") + "\n" + line + "\n"
        gate.commit(cfg, seg, "thread", "threads.md", text_out, f"segment {seg.sid[:8]}")
    print(f"Juno: thread opened in threads.md:\n{line}")
    return 0


def do_list(cfg: config.Config, args: argparse.Namespace) -> int:
    lines = thread_lines(store.read_text(os.path.join(cfg.vault, "threads.md")))
    if args.stale:
        lines = threadscan.stale_lines(lines, time.time(), max(args.days, 1))
        empty = f"Juno: no stale threads (older than {args.days} days)."
    else:
        empty = "Juno: no open threads."
    write.emit(args, {"threads": lines}, "\n".join(lines) if lines else empty)
    return 0


def do_pending(cfg: config.Config, args: argparse.Namespace) -> int:
    items = queues.pending(cfg, queues.THREADS)
    data = [{k: i.get(k) for k in ("id", "line", "signals", "t")} for i in items]
    text = "\n".join(
        f"{i['id']} · {i.get('line')} · signals: {'; '.join(i.get('signals') or [])}" for i in items
    )
    write.emit(args, data, text or "Juno: no pending thread candidates.")
    return 0


def remove_lines(cfg: config.Config, path: str, items: list[queues.Item]) -> set[str]:
    wanted = {str(i["id"]): str(i.get("line") or "") for i in items}
    kept: list[str] = []
    closed: set[str] = set()
    for line in store.read_text(path).splitlines():
        hit = next(
            (cid for cid, want in wanted.items() if cid not in closed and line.rstrip() == want),
            None,
        )
        if hit:
            closed.add(hit)
        else:
            kept.append(line)
    if closed:
        store.backup(cfg, path)
        fs.atomic_write(path, ("\n".join(kept) + "\n").encode("utf-8"))
    return closed


def close_line(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    if args.ids:
        raise Refused("give candidate ids or --line, not both")
    if args.line is None or not args.was or not args.evidence:
        raise Refused("the direct form needs --line, --was and --evidence together")
    evidence = gate.anchor(cfg, seg, args.evidence, user_only=True)
    with write.locked(cfg):
        rel, lines, index = fix.locate(cfg, f"threads.md:{args.line}", args.was, forget=False)
        gone = lines[index]
        if not THREAD_RE.match(gone.rstrip()):
            raise Refused(f"line {args.line} of threads.md is not a thread line")
        del lines[index]
        res = gate.commit(
            cfg, seg, "thread-close", rel, writegate.join(lines), evidence, run_cap=False
        )
    ledgerio.record(cfg, seg, {"ev": "thread_closed", "count": 1, "id": res.id})
    print(f"Juno: thread closed: {gone}")
    return gateio.report(res, "thread removed from")


def do_close(cfg: config.Config, args: argparse.Namespace) -> int:
    if args.line is not None or args.was or args.evidence:
        return close_line(cfg, args)
    if not args.ids:
        raise Refused("give candidate ids, or --line N --was TEXT --evidence YOUR_WORDS")
    seg = write.guard(cfg)
    ids = queues.clean_ids(queues.THREADS, args.ids)
    with queues.locked(cfg, queues.THREADS):
        queue = queues.read(cfg, queues.THREADS)
        items = queues.pending_by_id(queue, queues.THREADS, ids)
        write.require_chosen(cfg, seg, items)
        path = threads_path(cfg)
        with write.locked(cfg):
            closed = remove_lines(cfg, path, items)
        for item in items:
            queues.mark([item], "closed" if item["id"] in closed else "gone")
        queues.write(cfg, queues.THREADS, queue)
    ledgerio.record(cfg, seg, {"ev": "thread_closed", "count": len(closed)})
    for item in items:
        if item["id"] in closed:
            print(f"Juno: thread {item['id']} closed: {item.get('line')}")
        else:
            print(f"Juno: thread {item['id']} was no longer in threads.md: {item.get('line')}")
    return 0


def do_keep(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    ids = queues.clean_ids(queues.THREADS, args.ids)
    with queues.locked(cfg, queues.THREADS):
        queue = queues.read(cfg, queues.THREADS)
        queues.mark(queues.pending_by_id(queue, queues.THREADS, ids), "kept")
        queues.write(cfg, queues.THREADS, queue)
    ledgerio.record(cfg, seg, {"ev": "thread_kept", "count": len(ids)})
    print(f"Juno: threads kept open: {', '.join(ids)}.")
    return 0


ACTIONS = {
    "add": do_add,
    "list": do_list,
    "pending": do_pending,
    "close": do_close,
    "keep": do_keep,
}


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return write.execute(args, env, lambda cfg: ACTIONS[args.action](cfg, args))
