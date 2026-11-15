import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import gate, repos
from juno.adapters.vaultview import DiskVault
from juno.cmd import gateio, write
from juno.core import owners, taskfile, vaultfmt, writegate
from juno.core.errors import Refused

CURRENT, DECISIONS = "Current", "Decisions"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--topic", required=True, help="decision topic (slug)")
    parser.add_argument(
        "--supersedes",
        type=int,
        metavar="N",
        help="move line N of the owner's decisions to archive/decisions/",
    )
    gateio.evidence_option(parser, "your own words that made the decision, verbatim")
    parser.add_argument("text", help="the decision")


def base(topic: str, day: str) -> str:
    head = writegate.frontmatter({"topic": topic, "updated": day, "valid_from": day})
    return f"{head}\n## {CURRENT}\n"


def take_line(text: str, section: str, number: int) -> tuple[str, str]:
    """Remove line `number` (1-based over the `- ` lines) of `## section`; return text and line."""
    if number < 1:
        raise Refused(f"--supersedes {number}: lines are numbered from 1")
    lines = text.splitlines()
    start = owners.heading_index(lines, section)
    if start is None:
        raise Refused(f"no ## {section} section to supersede from")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    items = [i for i in range(start + 1, end) if lines[i].startswith("- ")]
    if number > len(items):
        raise Refused(f"--supersedes {number}: ## {section} has {len(items)} lines")
    old = lines.pop(items[number - 1])
    return writegate.join(lines), old


def visible_profiles(view: DiskVault) -> list[str]:
    return [n[:-3] for n in view.names("projects") if not view.hidden(f"projects/{n}")]


def refuse_unmigrated(cfg: config.Config, owner: owners.Owner) -> None:
    if owner.kind == "topic":
        return
    for rel in owners.legacy_decision_rels(owner.kind, owner.topic):
        if "- " in vaultfmt.decision_current(gate.read(cfg, rel)):
            raise Refused(
                f"--supersedes: {rel} still holds decisions for {owner.rel}; "
                "run juno migrate --decisions first"
            )


def owner_text(cfg: config.Config, owner: owners.Owner, old: str, day: str) -> tuple[str, str]:
    """The owner file to edit and the section the lines live in."""
    if owner.kind == "topic":
        return old or base(owner.topic, day), CURRENT
    if owner.kind == "ticket" and not old:
        url = taskfile.default_url(cfg.get_str("jira", "base_url"), owner.topic)
        return taskfile.stub(owner.topic, day, url), DECISIONS
    return owners.ensure_section(old, DECISIONS, first=True), DECISIONS


def stamp(owner: owners.Owner, text: str, day: str) -> str:
    if owner.kind == "ticket":
        return taskfile.merge(text, owner.topic, {}, day)
    return writegate.set_field(text, "updated", day)


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    view = DiskVault(cfg)
    owner = owners.decision_owner(args.topic, visible_profiles(view), repos.tickets_from(cfg))
    text = gate.scan_line(cfg, args.text, "decision")
    pid = gate.anchor(cfg, seg, args.evidence, user_only=True).split()[-1]
    day = write.today()
    line = f"- {text} (user {pid}, {day})"
    preset = write.preset(cfg)
    supersedes = args.supersedes
    with write.locked(cfg):
        old = gate.read(cfg, owner.rel)
        current, section = owner_text(cfg, owner, old, day)
        gone = None
        if supersedes is not None:
            refuse_unmigrated(cfg, owner)
            current, gone = take_line(current, section, supersedes)
        new, _ = writegate.add_item(current, line, preset, section=section)
        if new != old:
            new = stamp(owner, new, day)
        res = gate.commit(cfg, seg, "decision", owner.rel, new, f"user {pid}")
        if gone is not None and res.id:
            archive = owners.archive_rel(owner.topic)
            kept = owners.superseded_line(gone, day, owner.rel, res.id)
            gate.commit(
                cfg,
                seg,
                "decision-archive",
                archive,
                owners.archive_append(gate.read(cfg, archive), owner.topic, [kept]),
                f"user {pid}",
                extra={"link": res.id},
                run_cap=False,
            )
    return gateio.report(res, "decision written to")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
