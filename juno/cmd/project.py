import argparse
import os
import re
from collections.abc import Mapping

from juno import config
from juno.adapters import gate
from juno.cmd import backlog, gateio, write
from juno.core import rulepolicy, runtimeblock, vaultfmt, writegate
from juno.core.errors import Refused
from juno.core.ledger import Segment

SECTIONS = {"purpose": "Purpose", "run": "How to run", "gotchas": "Gotchas", "links": "Links"}
LINK_RE = re.compile(r"^\[\[([^\[\]|#\n]+)\]\](?: [^\n]{3,200})?$")
PURPOSE_LINES = 3
SKELETON = "\n## Purpose\n\n## How to run\n\n## Gotchas\n\n## Links\n"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("section", choices=[*sorted(SECTIONS), "runtime"])
    parser.add_argument("--repo", required=True)
    gateio.evidence_option(parser)
    parser.add_argument(
        "--block-file", help="runtime: the `runtime:` block, a file under /tmp or the state dir"
    )
    parser.add_argument(
        "text", nargs="?", help="one short factual line; links: `[[target]] reason`"
    )


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    if not rulepolicy.REPO_NAME_RE.fullmatch(args.repo):
        raise Refused(f"{args.repo!r} is not a repo name")
    evidence = gate.anchor(cfg, seg, args.evidence, user_only=False)
    day = write.today()
    if args.section == "runtime":
        return do_runtime(cfg, seg, args, evidence, day)
    if not args.text:
        raise Refused("a profile line is required")
    text = gate.scan_line(cfg, args.text, "profile line")
    if args.section == "links":
        match = LINK_RE.match(text)
        if not match:
            raise Refused("a link line is `[[target]] reason` (reason 3-200 characters)")
        if not os.path.isfile(gate.vault_file(cfg, match.group(1).strip() + ".md")):
            raise Refused(f"link target {match.group(1)!r} is not a vault file")
    name = SECTIONS[args.section]
    rel = f"projects/{args.repo}.md"
    base = writegate.frontmatter({"repo": args.repo, "updated": day}) + SKELETON
    line = f"- {text} · {day}"
    with write.locked(cfg):
        old = gate.read(cfg, rel)
        have = sum(1 for x in vaultfmt.section(old or base, name).splitlines() if x[:2] == "- ")
        new, status = writegate.add_item(old, line, write.preset(cfg), section=name, base=base)
        if args.section == "purpose" and status == "added" and have >= PURPOSE_LINES:
            raise Refused(f"Purpose already has {PURPOSE_LINES} lines; fix one with `juno fix`")
        if new != old:
            new = writegate.set_field(new, "updated", day)
        res = gate.commit(cfg, seg, "project", rel, new, evidence)
    return gateio.report(res, "profile line written to")


def do_runtime(
    cfg: config.Config, seg: Segment, args: argparse.Namespace, evidence: str, day: str
) -> int:
    if not args.block_file:
        raise Refused("runtime needs --block-file")
    block = runtimeblock.parse(backlog.read_body_text(cfg, args.block_file))
    rel = f"projects/{args.repo}.md"
    with write.locked(cfg):
        old = gate.read(cfg, rel)
        new = writegate.set_field(runtimeblock.apply(old, block, args.repo, day), "updated", day)
        res = gate.commit(cfg, seg, "project", rel, new, evidence)
    return gateio.report(res, "runtime block written to")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
