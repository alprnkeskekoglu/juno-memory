"""Settle the native-memory import: rule and self candidates, accepted or rejected together."""

import argparse
import time

from juno import config
from juno.adapters import gate, ledgerio, queues
from juno.cmd import rule, write
from juno.core import candidates, nativememory, selfguard, selfmodel
from juno.core.errors import Refused
from juno.core.ledger import Segment

Pair = tuple[list[queues.Item], list[queues.Item]]


def is_self(cid: str) -> bool:
    return cid.startswith(queues.SELF.letter)


def pick(args: argparse.Namespace, rules: list[queues.Item], mine: list[queues.Item]) -> Pair:
    """The pending rule and self candidates named by ids, or the whole batch without ids."""
    ids = [value.strip().casefold() for value in args.ids]
    if not ids:
        batch = (
            [i for i in rules if i.get("status") == "pending" and candidates.in_import_batch(i)],
            [i for i in mine if i.get("status") == "pending" and candidates.in_import_batch(i)],
        )
        if not batch[0] and not batch[1]:
            raise Refused(f"no pending candidates in batch {nativememory.BATCH}")
        return batch
    rule_ids = queues.clean_ids(queues.RULES, [i for i in ids if not is_self(i)])
    self_ids = queues.clean_ids(queues.SELF, [i for i in ids if is_self(i)])
    return (
        queues.pending_by_id(rules, queues.RULES, rule_ids),
        queues.pending_by_id(mine, queues.SELF, self_ids),
    )


def accept_self(cfg: config.Config, seg: Segment, item: queues.Item) -> tuple[str, bool]:
    """Write one self line through the self write machinery (caller holds the vault lock)."""
    source = gate.import_evidence(str(item.get("source") or ""))
    name = str(item.get("file") or "")
    if name not in selfmodel.FILES:
        raise Refused(f"self candidate {item['id']} names no self file")
    text = gate.scan_line(cfg, str(item.get("text") or ""), "self line")
    problem = selfguard.content_problem(text, text)
    if problem:
        raise Refused(f"self line refused ({problem}): record working behaviour only")
    rel, today = f"self/{name}.md", write.today()
    line = selfmodel.import_line(text, today, source)
    old = gate.read(cfg, rel)
    new, _ = selfmodel.record(old, line, write.preset(cfg), today, f"# {name.capitalize()}\n\n")
    res = gate.commit(
        cfg, seg, "self", rel, new, source, run_cap=False, extra={"candidate": item["id"]}
    )
    return rel, res.changed


def accept(
    cfg: config.Config, seg: Segment, rules: list[queues.Item], mine: list[queues.Item]
) -> None:
    done: list[tuple[queues.Item, rule.Accepted]] = []
    wrote: list[tuple[queues.Item, str, bool]] = []
    try:
        with write.locked(cfg):
            for item in rules:
                done.append((item, rule.accept_one(cfg, item)))
                queues.mark([item], "accepted", time.time())
            for item in mine:
                rel, changed = accept_self(cfg, seg, item)
                wrote.append((item, rel, changed))
                queues.mark([item], "accepted", time.time())
    finally:
        for item, result in done:
            rule.report_accepted(cfg, seg, item, result)
        for item, rel, changed in wrote:
            where = f"added to {rel}: {item['text']}" if changed else f"already present in {rel}"
            print(f"Juno: self candidate {item['id']} {where}")


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    with queues.locked(cfg, queues.RULES), queues.locked(cfg, queues.SELF):
        rule_queue = queues.read(cfg, queues.RULES)
        self_queue = queues.read(cfg, queues.SELF)
        rules, mine = pick(args, rule_queue, self_queue)
        every = [*rules, *mine]
        try:
            if args.action == "reject":
                queues.mark(every, "rejected")
                ledgerio.record(cfg, seg, {"ev": "rule_rejected", "count": len(every)})
                print(
                    f"Juno: import candidates rejected: {', '.join(str(i['id']) for i in every)}."
                )
            else:
                if args.ids:
                    write.require_chosen(cfg, seg, every)
                elif not write.chosen(cfg, seg, f"accept all {nativememory.BATCH}"):
                    raise Refused(
                        f"the user did not choose {nativememory.BATCH} in this segment: the "
                        f"option label 'Accept all {nativememory.BATCH}' must be an "
                        "AskUserQuestion answer the user chose"
                    )
                accept(cfg, seg, rules, mine)
        finally:
            queues.write(cfg, queues.RULES, rule_queue)
            queues.write(cfg, queues.SELF, self_queue)
    return 0
