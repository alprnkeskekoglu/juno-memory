import argparse
import os
import time
from collections.abc import Mapping

from juno import config
from juno.adapters import fs, ledgerio, queues, repos, store
from juno.cmd import write
from juno.cmd.write import Refused
from juno.core import backlog as bl
from juno.core import fold
from juno.core.ledger import Segment

LIST_CAP = 10
SECTION_CHOICES = tuple(bl.SECTION_KEYS)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    add = sub.add_parser("add", help="add an item from the user's quote and a body file")
    add.add_argument("--quote", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--priority", required=True, choices=bl.PRIORITIES)
    add.add_argument("--due")
    add.add_argument("--source")
    add.add_argument("--ticket", action="append")
    add.add_argument("--repo", action="append")
    add.add_argument("--body-file", required=True)
    lst = sub.add_parser("list", help="list open items and pending suggestions")
    lst.add_argument("--all", action="store_true")
    lst.add_argument("--status")
    lst.add_argument("--priority")
    lst.add_argument("--json", action="store_true")
    show = sub.add_parser("show", help="print one item")
    show.add_argument("--numbered", action="store_true")
    change = sub.add_parser("set", help="change status, priority, due, tickets or repos")
    change.add_argument("--status")
    change.add_argument("--priority")
    change.add_argument("--due")
    change.add_argument("--ticket", action="append")
    change.add_argument("--untag-ticket", action="append")
    change.add_argument("--repo", action="append")
    change.add_argument("--untag-repo", action="append")
    done = sub.add_parser("done", help="mark an item done")
    drop = sub.add_parser("drop", help="drop an item")
    drop.add_argument("--reason")
    for verb in (show, change, done, drop):
        verb.add_argument("slug")
    for verb in (change, done, drop):
        verb.add_argument("--quote", required=True)
    log = sub.add_parser("log", help="append a log line")
    log.add_argument("slug")
    log.add_argument("text")
    add_to = sub.add_parser("add-to", help="append lines to a section")
    edit = sub.add_parser("edit", help="replace or delete one section line")
    for verb in (add_to, edit):
        verb.add_argument("slug")
        verb.add_argument("--quote", required=True)
        verb.add_argument("--section", required=True, choices=SECTION_CHOICES)
    add_to.add_argument("--body-file", required=True)
    edit.add_argument("--line", required=True, type=int)
    edit.add_argument("--replace-file")
    edit.add_argument("--delete", action="store_true")
    for name in ("accept", "reject"):
        verb = sub.add_parser(name, help=f"{name} backlog suggestions")
        verb.add_argument("ids", nargs="+", metavar="ID")


def contained(cfg: config.Config, sub: str, name: str) -> str | None:
    return store.vault_file(cfg, sub, name)


def item_path(cfg: config.Config, slug: str) -> str:
    path = contained(cfg, "backlog", bl.check_slug(slug) + ".md")
    if path is None:
        raise Refused(f"backlog/{slug}.md is a symlink or outside the vault")
    return path


def read_item(cfg: config.Config, slug: str) -> tuple[str, str]:
    path = item_path(cfg, slug)
    if not os.path.isfile(path):
        raise Refused(f"no backlog item {slug} (see `juno backlog list --all`)")
    return path, store.read_text(path)


def write_item(cfg: config.Config, path: str, text: str) -> None:
    store.backup(cfg, path)
    fs.atomic_write(path, text.encode("utf-8"))


def rel(cfg: config.Config, path: str) -> str:
    return store.rel(cfg, path)


def load_items(cfg: config.Config) -> list[bl.Row]:
    directory = os.path.join(cfg.vault, "backlog")
    if os.path.islink(directory) or not os.path.isdir(directory):
        return []
    rows: list[bl.Row] = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".md") or name.startswith("."):
            continue
        path = contained(cfg, "backlog", name)
        text = store.read_text(path) if path else ""
        rows.append(bl.item_row(name[:-3], text, path is not None))
    return rows


def open_items(cfg: config.Config) -> list[bl.Row]:
    return [r for r in load_items(cfg) if not r["problem"] and r["status"] not in bl.CLOSED]


def body_roots(cfg: config.Config) -> list[str]:
    return [os.path.realpath("/tmp"), os.path.realpath(cfg.state)]


def read_body_text(cfg: config.Config, raw: str) -> str:
    path = os.path.abspath(raw)
    if os.path.islink(path) or not os.path.isfile(path):
        raise Refused(f"--body-file {raw} is not a regular file")
    real = os.path.realpath(path)
    if store.in_vault(cfg, real) or not any(store.is_within(real, r) for r in body_roots(cfg)):
        raise Refused("--body-file must live under /tmp or the Juno state dir")
    if os.path.getsize(real) > bl.BODY_BYTES:
        os.unlink(real)
        raise Refused(f"--body-file is over {bl.BODY_BYTES} bytes")
    text = store.read_text(real)
    os.unlink(real)
    return text


def verified_quote(cfg: config.Config, seg: Segment, raw: str) -> tuple[str, str]:
    quote = write.clean_line(cfg, fold.nfc(raw), "quote")
    minimum = cfg.get_int("caps", "quote_min_chars")
    if len(quote) < minimum:
        raise Refused(f"quote is shorter than {minimum} characters")
    return write.find_prompt(cfg, seg, quote), quote


def link_ticket(cfg: config.Config, key: str, slug: str) -> bool:
    path = contained(cfg, "tasks", key + ".md")
    if path is None or not os.path.isfile(path):
        return False
    text = store.read_text(path)
    line = bl.NOTE_LINE % slug
    if line in text:
        return False
    write_item(cfg, path, bl.append_line(text, "Notes", line))
    return True


def record(cfg: config.Config, seg: Segment, ev: str, slug: str) -> None:
    ledgerio.record(cfg, seg, {"ev": ev, "slug": slug})


def linked_note(linked: list[str]) -> str:
    return "\nlinked from: " + ", ".join(f"tasks/{k}.md" for k in linked) if linked else ""


def do_add(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    tickets = repos.tickets_from(cfg)
    sections = bl.parse_body(read_body_text(cfg, args.body_file))
    pid, quote = verified_quote(cfg, seg, args.quote)
    fields: dict[str, object] = {
        "title": bl.check_title(args.title),
        "priority": args.priority,
        "due": bl.check_date(args.due) if args.due else None,
        "source": bl.check_source(args.source),
        "tickets": bl.check_tickets(args.ticket, tickets),
        "repos": bl.check_repos(args.repo),
        "added": write.today(),
        "src": f'user {pid[:8]} · "{quote.replace(chr(34), chr(39))}"',
    }
    due = f", due {fields['due']}" if fields["due"] else ""
    entry = bl.log_entry(write.today(), f"added ({fields['priority']}{due})")
    with write.locked(cfg):
        folded = fold.fold(str(fields["title"]), write.preset(cfg))
        same = next(
            (
                r["slug"]
                for r in open_items(cfg)
                if fold.fold(str(r["title"]), write.preset(cfg)) == folded
            ),
            None,
        )
        if same:
            raise Refused(
                f"an open item has the same title: {same} "
                "(change it with `juno backlog set` or `log`)"
            )
        slug = bl.new_slug(
            str(fields["title"]),
            lambda s: os.path.lexists(item_path(cfg, s)),
            write.preset(cfg),
        )
        path = item_path(cfg, slug)
        fs.atomic_write(path, bl.render_item(fields, sections, entry).encode("utf-8"))
        keys = fields["tickets"]
        linked = [k for k in keys if link_ticket(cfg, k, slug)] if isinstance(keys, list) else []
    record(cfg, seg, "backlog_added", slug)
    print(f"Juno: backlog item added: {rel(cfg, path)}{linked_note(linked)}")
    return 0


def do_change(
    cfg: config.Config,
    args: argparse.Namespace,
    status: str | None = None,
    reason: str | None = None,
) -> int:
    seg = write.guard(cfg)
    tickets = repos.tickets_from(cfg)
    slug = bl.check_slug(args.slug)
    verified_quote(cfg, seg, args.quote)
    status = status or getattr(args, "status", None)
    status = bl.check_choice(status, bl.STATUSES, "--status") if status else None
    given = getattr(args, "priority", None)
    priority = bl.check_choice(given, bl.PRIORITIES, "--priority") if given else None
    due = getattr(args, "due", None)
    due = due if due in (None, "none") else bl.check_date(due)
    add_t = bl.check_tickets(getattr(args, "ticket", None), tickets)
    del_t = bl.check_tickets(getattr(args, "untag_ticket", None), tickets)
    add_r = bl.check_repos(getattr(args, "repo", None))
    del_r = bl.check_repos(getattr(args, "untag_repo", None))
    if set(add_t) & set(del_t) or set(add_r) & set(del_r):
        raise Refused("the same ticket or repo is both linked and unlinked")
    if not (status or priority or due or add_t or del_t or add_r or del_r):
        raise Refused(
            "nothing to change; give --status, --priority, --due, --ticket, --untag-ticket, "
            "--repo or --untag-repo"
        )
    with write.locked(cfg):
        path, text = read_item(cfg, slug)
        bl.head_span(text)
        text, notes = bl.apply_changes(
            text, write.today(), status, priority, due, add_t, reason, del_t, add_r, del_r
        )
        if notes:
            write_item(cfg, path, text)
        linked = [k for k in add_t if link_ticket(cfg, k, slug)]
    if not notes:
        print(f"Juno: no change to {rel(cfg, path)}.")
        return 0
    record(cfg, seg, "backlog_changed", slug)
    print(f"Juno: {rel(cfg, path)} updated: {'; '.join(notes)}{linked_note(linked)}")
    return 0


def do_set(cfg: config.Config, args: argparse.Namespace) -> int:
    return do_change(cfg, args)


def do_done(cfg: config.Config, args: argparse.Namespace) -> int:
    return do_change(cfg, args, "done")


def do_drop(cfg: config.Config, args: argparse.Namespace) -> int:
    reason = bl.checked_line(args.reason, "reason", 3, bl.REASON_CHARS) if args.reason else None
    return do_change(cfg, args, "dropped", reason)


def do_log(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    slug = bl.check_slug(args.slug)
    text = bl.checked_line(args.text, "log text", *bl.LOG_CHARS)
    entry = bl.log_entry(write.today(), text)
    with write.locked(cfg):
        path, current = read_item(cfg, slug)
        bl.head_span(current)
        updated = bl.set_field(current, "updated", write.today())
        write_item(cfg, path, bl.append_line(updated, "Log", entry))
    record(cfg, seg, "backlog_logged", slug)
    print(f"Juno: logged in {rel(cfg, path)}: {entry}")
    return 0


def do_add_to(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    slug = bl.check_slug(args.slug)
    heading = bl.SECTION_KEYS[args.section]
    lines = bl.parse_addition(read_body_text(cfg, args.body_file))
    verified_quote(cfg, seg, args.quote)
    with write.locked(cfg):
        path, text = read_item(cfg, slug)
        bl.head_span(text)
        status = bl.field(text, "status")
        text = bl.set_field(bl.append_section(text, heading, lines), "updated", write.today())
        entry = bl.log_entry(write.today(), f"{args.section} updated")
        write_item(cfg, path, bl.append_line(text, "Log", entry))
    record(cfg, seg, "backlog_changed", slug)
    closed = f" (note: the item is {status})" if status in bl.CLOSED else ""
    print(f"Juno: {len(lines)} line(s) added to ## {heading} of {rel(cfg, path)}{closed}")
    return 0


def do_edit(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    slug = bl.check_slug(args.slug)
    heading = bl.SECTION_KEYS[args.section]
    if (args.replace_file is None) == (not args.delete):
        raise Refused("give exactly one of --replace-file F or --delete")
    if args.line < 1:
        raise Refused("--line must be 1 or more")
    new_lines = (
        bl.parse_addition(read_body_text(cfg, args.replace_file)) if args.replace_file else []
    )
    verified_quote(cfg, seg, args.quote)
    verb = "replaced" if args.replace_file else "deleted"
    with write.locked(cfg):
        path, text = read_item(cfg, slug)
        bl.head_span(text)
        text = bl.set_field(
            bl.edit_section(text, heading, args.line, new_lines), "updated", write.today()
        )
        entry = bl.log_entry(write.today(), f"{args.section} line {args.line} {verb}")
        write_item(cfg, path, bl.append_line(text, "Log", entry))
    record(cfg, seg, "backlog_changed", slug)
    print(f"Juno: ## {heading} line {args.line} {verb} in {rel(cfg, path)}")
    return 0


def row_text(row: bl.Row) -> str:
    overdue = row["due"] and str(row["due"]) < write.today() and row["status"] not in bl.CLOSED
    return (
        f"{row['slug']} · {row['status']} · {row['priority']} · due {row['due'] or '-'}"
        f"{' (overdue)' if overdue else ''} · {row['title']}"
    )


def suggestion_text(item: queues.Item) -> str:
    status = f" · status -> {item['new_status']}" if item.get("new_status") else ""
    target = {"new": "new item", "note": "note"}.get(str(item.get("kind")), str(item.get("slug")))
    origin = f"segment {item['seg8']}" if item.get("seg8") else "queue"
    return f"{item['id']} · {target}{status} · {item.get('log')} ({origin})"


def do_list(cfg: config.Config, args: argparse.Namespace) -> int:
    everything = load_items(cfg)
    bad = [r for r in everything if r["problem"]]
    rows = [
        r for r in everything if not r["problem"] and (args.all or r["status"] not in bl.CLOSED)
    ]
    if args.status:
        rows = [r for r in rows if r["status"] == args.status.casefold()]
    if args.priority:
        rows = [r for r in rows if r["priority"] == args.priority.casefold()]
    rows.sort(key=bl.order_key)
    pending = queues.pending(cfg, queues.BACKLOG)
    lines = [row_text(r) for r in rows] or ["no backlog items" + ("" if args.all else " open")]
    if bad:
        names = ", ".join(str(r["slug"]) for r in bad[:LIST_CAP])
        lines.append(
            f"{len(bad)} item file(s) with invalid frontmatter: {names} (see `juno doctor`)"
        )
    if pending:
        lines.append(
            "Pending suggestions from session ends (accept or reject with "
            "`juno backlog accept|reject <ids>`; a new item is added with `juno backlog add` "
            "first, a note is written per protocol/vault-files.md first):"
        )
        lines += ["  " + suggestion_text(i) for i in pending]
    keys = ("id", "kind", "slug", "log", "new_status", "seg8", "source", "t")
    data = {
        "items": [{k: v for k, v in r.items() if k != "problem"} for r in rows],
        "invalid": [r["slug"] for r in bad],
        "suggestions": [{k: i.get(k) for k in keys} for i in pending],
    }
    write.emit(args, data, "\n".join(lines))
    return 0


def do_show(cfg: config.Config, args: argparse.Namespace) -> int:
    _, text = read_item(cfg, bl.check_slug(args.slug))
    print((bl.numbered(text) if args.numbered else text).rstrip("\n"))
    return 0


def accept_one(cfg: config.Config, item: queues.Item) -> str:
    path, text = read_item(cfg, str(item.get("slug") or ""))
    bl.head_span(text)
    log = bl.checked_line(str(item.get("log") or ""), "suggestion log", *bl.LOG_CHARS)
    raw = item.get("new_status")
    status = bl.check_choice(str(raw), bl.SUGGEST_STATUSES, "suggested status") if raw else None
    text, _ = bl.apply_changes(text, write.today(), status)
    text = bl.append_line(
        bl.set_field(text, "updated", write.today()), "Log", bl.log_entry(write.today(), log)
    )
    write_item(cfg, path, text)
    return path


def do_accept(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    ids = queues.clean_ids(queues.BACKLOG, args.ids)
    done: list[tuple[queues.Item, str | None]] = []
    with queues.locked(cfg, queues.BACKLOG):
        queue = queues.read(cfg, queues.BACKLOG)
        items = queues.pending_by_id(queue, queues.BACKLOG, ids)
        write.require_chosen(cfg, seg, items)
        try:
            with write.locked(cfg):
                for item in items:
                    plain = item.get("kind") in ("new", "note")
                    done.append((item, None if plain else accept_one(cfg, item)))
                    queues.mark([item], "accepted", time.time())
        finally:
            if done:
                queues.write(cfg, queues.BACKLOG, queue)
    for item, path in done:
        record(cfg, seg, "backlog_accepted", str(item.get("slug") or "-"))
        text = suggestion_text(item)
        if path is not None:
            print(f"Juno: suggestion {item['id']} applied to {rel(cfg, path)}: {text}")
        elif item.get("kind") == "note":
            print(
                f"Juno: suggestion {item['id']} closed; the note itself is written by the "
                f"session (protocol/vault-files.md): {text}"
            )
        else:
            print(
                f"Juno: suggestion {item['id']} closed; the item itself comes only from "
                f"`juno backlog add`: {text}"
            )
    return 0


def do_reject(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    ids = queues.clean_ids(queues.BACKLOG, args.ids)
    with queues.locked(cfg, queues.BACKLOG):
        queue = queues.read(cfg, queues.BACKLOG)
        queues.mark(queues.pending_by_id(queue, queues.BACKLOG, ids), "rejected")
        queues.write(cfg, queues.BACKLOG, queue)
    ledgerio.record(cfg, seg, {"ev": "backlog_rejected", "count": len(ids)})
    print(f"Juno: backlog suggestions rejected: {', '.join(ids)}.")
    return 0


ACTIONS = {
    "add": do_add,
    "add-to": do_add_to,
    "edit": do_edit,
    "set": do_set,
    "done": do_done,
    "drop": do_drop,
    "log": do_log,
    "list": do_list,
    "show": do_show,
    "accept": do_accept,
    "reject": do_reject,
}


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return write.execute(args, env, lambda cfg: ACTIONS[args.action](cfg, args))
