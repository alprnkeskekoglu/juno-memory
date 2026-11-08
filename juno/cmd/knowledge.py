import argparse
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from juno import config
from juno.adapters import gate
from juno.cmd import gateio, write
from juno.core import privacy, writegate
from juno.core.errors import Refused
from juno.core.ledger import Segment

URL_RE = re.compile(r"^https?://[^\s·]{4,200}$")
MAX_URLS = 2
SECOND_LEVEL = frozenset({"co", "com", "org", "net", "gov", "edu", "ac"})


def registrable_domain(url: str) -> str:
    # Approximation, no public-suffix list: last two labels, three under co.uk-style suffixes.
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    labels = host.split(".")
    if host.replace(".", "").isdigit() or len(labels) <= 2:
        return host
    if len(labels[-1]) == 2 and labels[-2] in SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    add = sub.add_parser("add", help="add a durable fact to knowledge/<topic>.md")
    add.add_argument("--topic", required=True, help="topic slug; one file per topic")
    add.add_argument("--source", choices=("session", "web"), default="session")
    add.add_argument(
        "--url",
        action="append",
        help="a page the fact came from (--source web); repeat once for a second source",
    )
    add.add_argument("--evidence", help="needed for --source session")
    add.add_argument("text", help="one fact that stays true for a month")


def provenance(cfg: config.Config, seg: Segment, args: argparse.Namespace) -> str:
    if args.source == "web":
        urls = args.url or []
        if not urls or any(
            not URL_RE.match(u) or privacy.scan(u, gate.prefixes(cfg)) for u in urls
        ):
            raise Refused("--source web needs --url https://... without secrets or personal data")
        if len(urls) > MAX_URLS:
            raise Refused(f"at most {MAX_URLS} --url values")
        if len({registrable_domain(u) for u in urls}) < len(urls):
            raise Refused("two --url values need different registrable domains")
        return "web " + ", ".join(urls)
    if not args.evidence:
        raise Refused("--evidence is required for --source session")
    return gate.anchor(cfg, seg, args.evidence, user_only=False)


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    seg = write.guard(cfg)
    topic = writegate.check_slug(args.topic, "topic")
    text = gate.scan_line(cfg, args.text, "fact")
    src = provenance(cfg, seg, args)
    day = write.today()
    rel = f"knowledge/{topic}.md"
    line = f"- {text} · src: {src} · {day}"
    head = writegate.frontmatter(
        {"topic": topic, "sources": f"[{src}]", "valid_from": day, "verified": day}
    )
    base = f"{head}\n# {topic}\n\n"
    with write.locked(cfg):
        new, _ = writegate.add_item(gate.read(cfg, rel), line, write.preset(cfg), base=base)
        res = gate.commit(cfg, seg, "knowledge", rel, new, src)
    return gateio.report(res, "fact written to")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return gateio.run_verb(args, env, do)
