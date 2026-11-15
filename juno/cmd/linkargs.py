import argparse

from juno import config
from juno.adapters import gate, linksio
from juno.core import linkline, links
from juno.core.errors import Refused


def add_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--link",
        action="append",
        metavar="TARGET:REASON",
        help=(
            "link a vault file from the final Links line; repeatable. TARGET is a file name "
            "without .md or a vault path that exists; REASON is "
            f"{linkline.MIN_REASON_WORDS}+ words and {linkline.MIN_REASON_CHARS}+ characters "
            "saying why (no ]], |, parentheses, newline or ' · '). A repeated target updates "
            "its reason"
        ),
    )


def prepare(
    cfg: config.Config, raw: list[str] | None
) -> tuple[list[linkline.Spec], linkline.Canon]:
    """The checked (target, reason) pairs and the function that resolves a target to its file."""
    index = links.build_index([rel for rel, _, _ in linksio.walk(cfg.vault)]) if raw else {}

    def canon(target: str) -> str | None:
        return links.resolve_link(target, index) if index else None

    specs: list[linkline.Spec] = []
    for item in raw or []:
        target, reason = linkline.parse_spec(item)
        reason = gate.scan_line(cfg, reason, "link reason")
        linkline.check_reason(reason)
        if canon(target) is None:
            raise Refused(f"link target {target!r} is not a vault file")
        specs.append((target, reason))
    return specs, canon
