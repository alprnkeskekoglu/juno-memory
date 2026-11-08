import argparse
import os
from collections.abc import Mapping

from juno import config
from juno.adapters import gate, store
from juno.cmd import gateio, write
from juno.core import fold, writegate
from juno.core.errors import Refused

MIN_CHARS = 20
SCAN_FILES = 50


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--slug", help="file name part (default: from the text)")
    parser.add_argument("--repos", help="comma separated repos")
    parser.add_argument("--tickets", help="comma separated ticket keys")
    parser.add_argument("text", help="the situational finding, written once")


def todays_duplicate(cfg: config.Config, day: str, text: str) -> str | None:
    directory = os.path.join(cfg.vault, "notes")
    if os.path.islink(directory) or not os.path.isdir(directory):
        return None
    preset = write.preset(cfg)
    for name in sorted(os.listdir(directory))[-SCAN_FILES:]:
        if name.startswith(day) and name.endswith(".md"):
            body = store.read_text(os.path.join(directory, name)).split("---\n")[-1]
            if writegate.duplicate([body.strip()], text, preset):
                return f"notes/{name}"
    return None


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    text = gate.scan_text(cfg, args.text, "note")
    if len(text) < MIN_CHARS:
        raise Refused(f"note must be at least {MIN_CHARS} characters")
    writegate.check_length(text, "note")
    slug = args.slug or fold.slug(text, write.preset(cfg))[:40].strip("-") or "note"
    writegate.check_slug(slug)
    day = write.today()
    rel = f"notes/{day}-{slug}.md"
    head = writegate.frontmatter(
        {
            "date": day,
            "repos": gateio.listing(gateio.names(args.repos, "repo")),
            "tickets": gateio.listing(gateio.names(args.tickets, "ticket")),
            "source": seg.sid[:8],
        }
    )
    with write.locked(cfg):
        if gate.read(cfg, rel):
            raise Refused(f"{rel} exists; notes are written once (use another --slug or fix)")
        same = todays_duplicate(cfg, day, text)
        if same:
            print(f"Juno: already recorded in {same}; nothing written")
            return 0
        res = gate.commit(
            cfg, seg, "note", rel, head + "\n" + text + "\n", f"segment {seg.sid[:8]}"
        )
    return gateio.report(res, "note written to")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
