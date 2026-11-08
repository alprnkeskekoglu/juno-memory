import argparse
import os
from collections.abc import Mapping
from typing import Any

from juno import config
from juno.adapters import gate, ledgerio, repos, store, vaultview
from juno.cmd import compact, write
from juno.core import profiles, vaultfmt
from juno.core.ledger import Segment


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true")


def load_tasks(cfg: config.Config) -> list[tuple[str, str]]:
    retired = vaultview.read_retired(cfg.state)
    found = []
    for name in compact.names(cfg, "tasks"):
        rel = f"tasks/{name}"
        if not compact.safe(cfg, rel):
            continue
        text = store.read_text(compact.vault_file(cfg, rel))
        if vaultfmt.hidden_reason(rel, text, retired) is None:
            found.append((name[:-3], text))
    return found


def run_profiles(cfg: config.Config, seg: Segment) -> dict[str, Any]:
    result: dict[str, Any] = {
        "updated": [],
        "unchanged": [],
        "skipped": {},
        "replaced_manual_block": {},
        "writes": {},
    }
    by_repo = profiles.entries_by_repo(load_tasks(cfg))
    for name in compact.names(cfg, "projects"):
        rel = f"projects/{name}"
        if not compact.safe(cfg, rel):
            result["skipped"][rel] = "unsafe path"
            continue
        try:
            with write.locked(cfg, write.VIEW_LOCK_SECONDS):
                outcome = one(cfg, seg, rel, by_repo)
        except write.Refused as error:
            result["skipped"][rel] = str(error)
            continue
        if outcome.skipped:
            result["skipped"][rel] = outcome.skipped
            continue
        result["unchanged" if outcome.write is None else "updated"].append(rel)
        if outcome.write:
            result["writes"][rel] = outcome.write
        if outcome.manual is not None:
            result["replaced_manual_block"][rel] = outcome.manual
    return result


class Outcome:
    def __init__(self, skipped: str = "", write: str | None = None, manual: str | None = None):
        self.skipped, self.write, self.manual = skipped, write, manual


def one(
    cfg: config.Config, seg: Segment, rel: str, by_repo: dict[str, list[tuple[str, str]]]
) -> Outcome:
    text = store.read_text(compact.vault_file(cfg, rel))
    entries: dict[str, str] = {}
    for stem in profiles.profile_names(os.path.basename(rel)[:-3], text):
        entries.update(by_repo.get(stem, []))
    body = profiles.tickets_body([entries[k] for k in sorted(entries)])
    new = profiles.rewrite(text, body)
    if new.text is None:
        return Outcome(skipped=new.skipped)
    res = gate.commit(cfg, seg, "profiles", rel, new.text, "engine: tickets block", engine=True)
    return Outcome(
        write=res.id if res.changed else None, manual=new.manual if res.changed else None
    )


def summary(result: dict[str, Any]) -> str:
    lines = [f"updated {rel} (write {wid})" for rel, wid in sorted(result["writes"].items())]
    lines += [f"skipped {rel}: {why}" for rel, why in sorted(result["skipped"].items())]
    lines += [
        f"WARNING replaced_manual_block: {rel} had a hand-written tickets block that was replaced"
        for rel in sorted(result["replaced_manual_block"])
    ]
    return "Juno profiles:\n" + ("\n".join(lines) if lines else "nothing to update")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    def body(cfg: config.Config) -> int:
        seg = write.guard(cfg)
        if not repos.tickets_from(cfg).enabled:
            write.emit(
                args, {"skipped": "jira not configured"}, "Juno: profiles: jira not configured"
            )
            return 0
        result = run_profiles(cfg, seg)
        ledgerio.record(cfg, seg, {"ev": "profiles"})
        write.emit(args, result, summary(result))
        return 0

    return write.execute(args, env, body)
