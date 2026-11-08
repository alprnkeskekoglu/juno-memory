import argparse
import os
import time
from collections.abc import Mapping

from juno import config
from juno.adapters import fs, ledgerio, queues, store
from juno.adapters import status as status_io
from juno.cmd import write
from juno.cmd.write import Refused
from juno.core import fold, rulepolicy, topics
from juno.core.ledger import Segment
from juno.core.vaultfmt import RuleEntry, rule_entries, rule_scope


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    add = sub.add_parser("add", help="add a rule with the user's own quote")
    add.add_argument("--scope", required=True, help="global, repo:<repo> or topic:<slug>")
    add.add_argument("--quote", required=True, help="verbatim words the user typed or chose")
    add.add_argument("--triggers", help="comma separated triggers (topic scopes)")
    add.add_argument("rule")
    lst = sub.add_parser("list", help="print every rule, numbered per file")
    lst.add_argument("--json", action="store_true")
    remove = sub.add_parser("remove", help="remove rule N of a scope")
    remove.add_argument("--scope", required=True)
    remove.add_argument("--quote", required=True)
    remove.add_argument("index", type=int)
    move = sub.add_parser("move", help="move rule N between scopes")
    move.add_argument("--from", dest="source", required=True)
    move.add_argument("--to", dest="target", required=True)
    move.add_argument("--triggers")
    move.add_argument("--quote", required=True)
    move.add_argument("index", type=int)
    trig = sub.add_parser("triggers", help="add triggers to a topic")
    trig.add_argument("--scope", required=True)
    trig.add_argument("--add", required=True)
    trig.add_argument("--quote", required=True)
    pend = sub.add_parser("pending", help="list pending rule candidates")
    pend.add_argument("--json", action="store_true")
    for name in ("accept", "reject"):
        verb = sub.add_parser(name, help=f"{name} rule candidates")
        verb.add_argument("ids", nargs="+", metavar="ID")


def vault_path(cfg: config.Config, relpath: str) -> str:
    return os.path.join(cfg.vault, *relpath.split("/"))


def write_text(cfg: config.Config, relpath: str, text: str) -> None:
    path = vault_path(cfg, relpath)
    store.backup(cfg, path)
    fs.atomic_write(path, text.encode("utf-8"))


def read(cfg: config.Config, relpath: str) -> str:
    return store.read_text(vault_path(cfg, relpath))


def rule_text(cfg: config.Config, raw: str) -> str:
    rule = write.clean_line(cfg, raw, "rule")
    if len(rule) > cfg.get_int("caps", "rule_max_chars"):
        raise Refused(f"rule is longer than {cfg.get_int('caps', 'rule_max_chars')} characters")
    return rule


def triggers_note(triggers: list[str] | None) -> str:
    return "\ntriggers: " + ", ".join(triggers) if triggers else ""


def caps_hit(cfg: config.Config, scope: rulepolicy.Scope, text: str, rule: str) -> bool:
    return rulepolicy.check_caps(
        scope.name,
        scope.relpath,
        text,
        cfg.get_int("caps", scope.cap_key),
        cfg.get_int("caps", "global_rules_chars"),
        rule,
    )


def added(cfg: config.Config, seg: Segment, scope: str, rule: str, candidate: str = "") -> None:
    rec: dict[str, object] = {"ev": "rule_added", "scope": scope}
    if candidate:
        rec["candidate"] = candidate
    ledgerio.record(cfg, seg, rec)
    status_io.update(cfg, seg, rule={"scope": scope, "text": rule})


def do_add(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    scope = rulepolicy.parse_scope(args.scope)
    rule = rule_text(cfg, args.rule)
    quote = write.clean_line(cfg, fold.nfc(args.quote), "quote")
    pid = write.match_quote(cfg, seg, quote, rule)
    line = rulepolicy.rule_line(write.today(), rule, pid, quote)
    with write.locked(cfg):
        text = read(cfg, scope.relpath)
        triggers = rulepolicy.topic_triggers(scope.name, text, args.triggers, write.preset(cfg))
        if caps_hit(cfg, scope, text, rule):
            print(f"Juno: rule already present in {scope.relpath}: {rule}")
            return 0
        write_text(cfg, scope.relpath, rulepolicy.append_line(text, scope.name, line, triggers))
    added(cfg, seg, scope.name, rule)
    print(f"Juno: rule added to {scope.relpath}:\n{line}{triggers_note(triggers)}")
    return 0


def rule_file_names(cfg: config.Config) -> list[str]:
    names: list[str] = []
    for sub in ("", f"{topics.REPO_DIR}/", f"{topics.TOPIC_DIR}/"):
        directory = vault_path(cfg, "rules/" + sub.rstrip("/")).rstrip(os.sep)
        if os.path.isdir(directory):
            names += [sub + n for n in sorted(os.listdir(directory)) if n.endswith(".md")]
    return names


def legacy_note(name: str) -> str:
    shaped = "/" not in name and rulepolicy.TICKET_SHAPE_RE.fullmatch(name[:-3])
    return " (legacy ticket rules; move them with `juno rule move`)" if shaped else ""


def do_list(cfg: config.Config, args: argparse.Namespace) -> int:
    names = rule_file_names(cfg)
    data: list[dict[str, object]] = []
    lines: list[str] = []
    for name in names:
        relpath = f"rules/{name}"
        text = read(cfg, relpath)
        triggers = (
            topics.file_triggers(text, write.preset(cfg)) if topics.topic_slug(name) else None
        )
        lines.append(relpath + legacy_note(name))
        if triggers is not None:
            lines.append("  triggers: " + (", ".join(triggers) or "none"))
        entries = rule_entries(text)
        lines += [f"  {i}. {e.line[2:]}" for i, e in enumerate(entries, 1)]
        data.append(
            {
                "file": relpath,
                "scope": rule_scope(relpath),
                "triggers": triggers,
                "rules": [e.line[2:] for e in entries],
            }
        )
    write.emit(args, {"files": data}, "\n".join(lines) if lines else "Juno: no rules yet.")
    return 0


def do_remove(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    scope = rulepolicy.parse_scope(args.scope)
    quote = write.clean_line(cfg, fold.nfc(args.quote), "quote")
    with write.locked(cfg):
        text = read(cfg, scope.relpath)
        entry = rulepolicy.entry_at(text, scope.relpath, args.index)
        write.match_quote(cfg, seg, quote, entry.text or entry.line)
        write_text(cfg, scope.relpath, rulepolicy.remove_line(text, entry.number))
    ledgerio.record(cfg, seg, {"ev": "rule_removed", "scope": scope.name})
    print(f"Juno: rule removed from {scope.relpath}:\n{entry.line}")
    return 0


def do_move(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    source = rulepolicy.parse_scope(args.source)
    target = rulepolicy.parse_scope(args.target)
    if source.relpath == target.relpath:
        raise Refused("--from and --to are the same scope")
    quote = write.clean_line(cfg, fold.nfc(args.quote), "quote")
    preset = write.preset(cfg)
    with write.locked(cfg):
        src_text = read(cfg, source.relpath)
        entry = rulepolicy.entry_at(src_text, source.relpath, args.index)
        if entry.text is None:
            raise Refused(f"rule {args.index} in {source.relpath} is not a well-formed rule line")
        named = [target.name.split(":", 1)[-1]]
        if args.triggers:
            named += rulepolicy.clean_triggers(args.triggers, preset)
        write.match_quote(cfg, seg, quote, " ".join([entry.text, *named]))
        dst_text = read(cfg, target.relpath)
        triggers = rulepolicy.topic_triggers(target.name, dst_text, args.triggers, preset)
        if caps_hit(cfg, target, dst_text, entry.text):
            raise Refused(
                f"the rule is already present in {target.relpath}; remove it from "
                f"{source.relpath} with `juno rule remove`"
            )
        write_text(
            cfg, target.relpath, rulepolicy.append_line(dst_text, target.name, entry.line, triggers)
        )
        drop_source(cfg, source, src_text, entry)
    ledgerio.record(cfg, seg, {"ev": "rule_moved", "scope": target.name, "from": source.name})
    print(
        f"Juno: rule moved from {source.relpath} to {target.relpath}:\n{entry.line}"
        f"{triggers_note(triggers)}"
    )
    return 0


def drop_source(cfg: config.Config, scope: rulepolicy.Scope, text: str, entry: RuleEntry) -> None:
    left = rulepolicy.remove_line(text, entry.number)
    if scope.name != "global" and not rulepolicy.has_rules(left):
        path = vault_path(cfg, scope.relpath)
        store.backup(cfg, path)
        os.unlink(path)
        return
    write_text(cfg, scope.relpath, left)


def do_triggers(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    scope = rulepolicy.parse_scope(args.scope)
    if not scope.name.startswith("topic:"):
        raise Refused("triggers belong to topic:<slug> scopes only")
    added_triggers = rulepolicy.clean_triggers(args.add, write.preset(cfg))
    quote = write.clean_line(cfg, fold.nfc(args.quote), "quote")
    words = " ".join([*added_triggers, scope.name[len("topic:") :]])
    write.match_quote(cfg, seg, quote, words)
    with write.locked(cfg):
        if not os.path.exists(vault_path(cfg, scope.relpath)):
            raise Refused(
                f"{scope.relpath} does not exist; create it with "
                f"`juno rule add --scope {scope.name} --triggers ...`"
            )
        text = read(cfg, scope.relpath)
        triggers = rulepolicy.topic_triggers(scope.name, text, args.add, write.preset(cfg)) or []
        write_text(cfg, scope.relpath, topics.set_triggers(text, scope.name, triggers))
    ledgerio.record(cfg, seg, {"ev": "rule_triggers", "scope": scope.name})
    print(f"Juno: triggers of {scope.relpath}: {', '.join(triggers)}")
    return 0


def do_pending(cfg: config.Config, args: argparse.Namespace) -> int:
    items = queues.pending(cfg, queues.RULES)
    keys = ("id", "scope", "triggers", "rule", "quote", "seg8", "pid", "t")
    data = [{k: i.get(k) for k in keys} for i in items]
    lines = []
    for i in items:
        topic = f" (new topic, triggers: {', '.join(i['triggers'])})" if i.get("triggers") else ""
        lines.append(
            f"{i['id']} · {i.get('scope')}{topic} · {i.get('rule')} · "
            f'src: user {i.get("pid")} (segment {i.get("seg8")}) · "{i.get("quote")}"'
        )
    write.emit(args, data, "\n".join(lines) if lines else "Juno: no pending rule candidates.")
    return 0


def accept_one(cfg: config.Config, item: queues.Item) -> tuple[str, str, bool, list[str] | None]:
    scope = rulepolicy.parse_scope(str(item.get("scope") or ""))
    rule = rule_text(cfg, str(item.get("rule") or ""))
    quote = write.clean_line(cfg, fold.nfc(str(item.get("quote") or "")), "quote")
    pid = str(item.get("pid") or "")
    if not pid or len(pid) > 8 or not all(c.isalnum() or c in "_-" for c in pid):
        raise Refused(f"rule candidate {item['id']} has no source prompt id")
    sid = item.get("sid")
    if sid and ledgerio.exists(cfg, sid):
        folded = fold.fold(quote, write.preset(cfg))
        sources = [t for p, t in ledgerio.fold(cfg, sid).quote_sources() if p[:8] == pid]
        if not any(folded in fold.fold(t, write.preset(cfg)) for t in sources):
            raise Refused(
                f"the quote of rule candidate {item['id']} is no longer a verbatim part of "
                f"prompt {pid} of segment {item.get('seg8')}"
            )
    text = read(cfg, scope.relpath)
    raw = (
        ",".join(item["triggers"])
        if scope.name.startswith("topic:") and item.get("triggers")
        else None
    )
    triggers = rulepolicy.topic_triggers(scope.name, text, raw, write.preset(cfg))
    line = rulepolicy.rule_line(write.today(), rule, pid, quote)
    present = caps_hit(cfg, scope, text, rule)
    if not present:
        write_text(cfg, scope.relpath, rulepolicy.append_line(text, scope.name, line, triggers))
    return scope.name, line, present, triggers


def do_accept(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    ids = queues.clean_ids(queues.RULES, args.ids)
    done: list[tuple[queues.Item, tuple[str, str, bool, list[str] | None]]] = []
    with queues.locked(cfg, queues.RULES):
        queue = queues.read(cfg, queues.RULES)
        items = queues.pending_by_id(queue, queues.RULES, ids)
        write.require_chosen(cfg, seg, items)
        try:
            with write.locked(cfg):
                for item in items:
                    done.append((item, accept_one(cfg, item)))
                    queues.mark([item], "accepted", time.time())
        finally:
            if done:
                queues.write(cfg, queues.RULES, queue)
    for item, (scope, line, present, triggers) in done:
        relpath = rulepolicy.parse_scope(scope).relpath
        if present:
            print(f"Juno: rule candidate {item['id']} already present in {relpath}: {item['rule']}")
        else:
            added(cfg, seg, scope, str(item["rule"]), str(item["id"]))
            print(
                f"Juno: rule candidate {item['id']} added to {relpath}:\n{line}"
                f"{triggers_note(triggers)}"
            )
    return 0


def do_reject(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    ids = queues.clean_ids(queues.RULES, args.ids)
    with queues.locked(cfg, queues.RULES):
        queue = queues.read(cfg, queues.RULES)
        queues.mark(queues.pending_by_id(queue, queues.RULES, ids), "rejected")
        queues.write(cfg, queues.RULES, queue)
    ledgerio.record(cfg, seg, {"ev": "rule_rejected", "count": len(ids)})
    print(f"Juno: rule candidates rejected: {', '.join(ids)}.")
    return 0


ACTIONS = {
    "add": do_add,
    "list": do_list,
    "remove": do_remove,
    "move": do_move,
    "triggers": do_triggers,
    "pending": do_pending,
    "accept": do_accept,
    "reject": do_reject,
}


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return write.execute(args, env, lambda cfg: ACTIONS[args.action](cfg, args))
