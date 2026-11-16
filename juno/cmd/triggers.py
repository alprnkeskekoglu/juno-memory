"""`juno triggers propose` (#36): queue a trigger candidate for every knowledge file without
`triggers:`, proposed from its title and body (no model call). Nothing reaches a knowledge
file until the user accepts the candidate with `juno rule accept`."""

import argparse
import time
from collections.abc import Mapping

from juno import config
from juno.adapters import queues
from juno.adapters.vaultview import DiskVault
from juno.cmd import write
from juno.core import candidates, topics, triggerprop, vaultfmt


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("propose", help="queue trigger candidates for knowledge files without triggers")


def proposals(cfg: config.Config) -> tuple[list[tuple[str, list[str]]], int]:
    """(file, triggers) for each untriggered, visible knowledge file, and how many of those
    had nothing to propose."""
    preset = cfg.get_str("language", "preset")
    view = DiskVault(cfg)
    found: list[tuple[str, list[str]]] = []
    empty = 0
    for name in view.names("knowledge"):
        rel = f"knowledge/{name}"
        text = view.read(rel)
        if not topics.KNOWLEDGE_RE.match(name) or view.hidden(rel, text):
            continue
        fields, body = vaultfmt.parse_frontmatter(text)
        if topics.field_list(fields.get("triggers"), preset):
            continue
        proposed = triggerprop.propose(str(fields.get("title") or ""), body, preset)
        if proposed:
            found.append((rel, proposed))
        else:
            empty += 1
    return found, empty


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    write.guard(cfg)
    preset = cfg.get_str("language", "preset")
    found, empty = proposals(cfg)
    now = time.time()

    def change(queue: list[queues.Item]) -> dict[str, int]:
        queue[:] = candidates.prune(queue, now)
        counts = {"queued": 0, "known": 0, "over": 0}
        for rel, triggers in found:
            counts[candidates.add_proposed_triggers(queue, rel, triggers, now, preset)] += 1
        return counts

    counts = queues.update(cfg, queues.RULES, change)
    print(
        f"Juno: {counts['queued']} trigger candidates queued, {counts['known']} already queued "
        f"or decided, {empty} files with nothing to propose"
    )
    if counts["over"]:
        print(
            f"Juno: {counts['over']} over the limit of {candidates.PROPOSE_MAX} pending trigger "
            "proposals were not queued; settle the pending ones, then run it again"
        )
    if counts["queued"]:
        print(
            "Juno: no knowledge file was changed; see `juno rule pending`, then "
            "`juno rule accept <id>` or `juno rule reject <id>` (the id must be in a prompt you "
            "typed or an answer you chose)"
        )
    return 0


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return write.execute(args, env, lambda cfg: do(cfg, args))
