import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import gate
from juno.cmd import gateio, write
from juno.core import writegate
from juno.core.errors import Refused

CURRENT, HISTORY = "Current", "History"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--topic", required=True, help="decision topic (slug)")
    parser.add_argument(
        "--supersedes", type=int, metavar="N", help="move Current line N to History"
    )
    gateio.evidence_option(parser, "your own words that made the decision, verbatim")
    parser.add_argument("text", help="the decision")


def base(topic: str, day: str) -> str:
    head = writegate.frontmatter({"topic": topic, "updated": day, "valid_from": day})
    return f"{head}\n## {CURRENT}\n\n## {HISTORY}\n"


def supersede(text: str, number: int, day: str, preset: str) -> str:
    lines = text.splitlines()
    start = next((i for i, x in enumerate(lines) if x.strip() == f"## {CURRENT}"), None)
    if start is None:
        raise Refused("no ## Current section to supersede from")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    items = [i for i in range(start + 1, end) if lines[i].startswith("- ")]
    if not 1 <= number <= len(items):
        raise Refused(f"--supersedes {number}: ## Current has {len(items)} lines")
    old = lines.pop(items[number - 1])
    moved, _ = writegate.add_item(
        writegate.join(lines), f"{old} · superseded {day}", preset, section=HISTORY
    )
    return moved


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    topic = writegate.check_slug(args.topic, "topic")
    text = gate.scan_line(cfg, args.text, "decision")
    pid = gate.anchor(cfg, seg, args.evidence, user_only=True).split()[-1]
    day = write.today()
    rel = f"decisions/{topic}.md"
    line = f"- {text} (user {pid}, {day})"
    preset = write.preset(cfg)
    with write.locked(cfg):
        old = gate.read(cfg, rel)
        current = old or base(topic, day)
        if args.supersedes:
            current = supersede(current, args.supersedes, day, preset)
        new, _ = writegate.add_item(current, line, preset, section=CURRENT)
        if new != old:
            new = writegate.set_field(new, "updated", day)
        res = gate.commit(cfg, seg, "decision", rel, new, f"user {pid}")
    return gateio.report(res, "decision written to")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
