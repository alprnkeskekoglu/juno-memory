import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import gate
from juno.cmd import gateio, write
from juno.core import writegate
from juno.core.errors import Refused

STATUSES = ("planned", "in-progress", "done", "dropped")
SKELETON = "\n## Why\n\n## Notes\n\n## Links\n\n## Outcome\n"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--slug", required=True)
    parser.add_argument("--status", choices=STATUSES)
    parser.add_argument("--tags", help="comma separated tags")
    parser.add_argument("--note", help="append a line to ## Notes")
    parser.add_argument("--outcome", help="where it shipped or why it was dropped")
    parser.add_argument("text", nargs="?", help="the idea in one line (new files)")


def create(cfg: config.Config, seg_id: str, args: argparse.Namespace, day: str) -> str:
    if not args.text:
        raise Refused("a new idea needs its one-line text")
    text = gate.scan_line(cfg, args.text, "idea")
    head = writegate.frontmatter(
        {
            "status": args.status or "planned",
            "created": day,
            "updated": day,
            "tags": gateio.listing(gateio.names(args.tags, "tag")),
            "source": seg_id,
        }
    )
    return f"{head}\n## Idea\n{text}\n{SKELETON}"


def update(cfg: config.Config, old: str, args: argparse.Namespace, day: str) -> str:
    new = old
    preset = write.preset(cfg)
    if args.status:
        new = writegate.set_field(new, "status", args.status)
    if args.note:
        line = f"- {gate.scan_line(cfg, args.note, 'note')} · {day}"
        new, _ = writegate.add_item(new, line, preset, section="Notes")
    if args.outcome:
        line = f"- {gate.scan_line(cfg, args.outcome, 'outcome')} · {day}"
        new, _ = writegate.add_item(new, line, preset, section="Outcome")
    if new == old:
        raise Refused("idea exists; give --status, --note or --outcome")
    return writegate.set_field(new, "updated", day)


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    slug = writegate.check_slug(args.slug)
    rel = f"ideas/{slug}.md"
    day = write.today()
    with write.locked(cfg):
        old = gate.read(cfg, rel)
        new = update(cfg, old, args, day) if old else create(cfg, seg.sid[:8], args, day)
        res = gate.commit(cfg, seg, "idea", rel, new, f"segment {seg.sid[:8]}")
    return gateio.report(res, "idea written to")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
