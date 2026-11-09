import re
from collections.abc import Callable

from juno.core import fold, routing
from juno.core.errors import Refused
from juno.core.ledger import Segment
from juno.core.toolrec import whole_key

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


def split_names(raw: str | None) -> list[str]:
    return list(dict.fromkeys(p.strip() for p in (raw or "").split(",") if p.strip()))


def link_keys(seg: Segment, links: str | None) -> list[str]:
    """Ticket keys written as [[KEY]] in a card's --links (configured prefixes only)."""
    found = (whole_key(seg.rules.tickets, m) for m in WIKILINK_RE.findall(links or ""))
    return list(dict.fromkeys(k for k in found if k))


def explicit_tickets(seg: Segment, raw: str | None, linked: list[str]) -> list[str]:
    if raw is None:
        return []
    names = split_names(raw)
    if not names:
        raise Refused("--tickets is empty")
    keys: list[str] = []
    for name in names:
        if not seg.rules.tickets.enabled:
            raise Refused("--tickets needs [jira] prefixes in the config")
        key = whole_key(seg.rules.tickets, name)
        if key is None:
            raise Refused(f"{name!r} is not a ticket key of the configured prefixes")
        if key not in seg.tickets and key not in linked:
            raise Refused(
                f"ticket {key} was not seen in this segment "
                "(typed, a tool call, a touched repo's branch, or a [[KEY]] in --links)"
            )
        keys.append(key)
    return list(dict.fromkeys(keys))


def explicit_repos(seg: Segment, raw: str | None, has_profile: Callable[[str], bool]) -> list[str]:
    if raw is None:
        return []
    names = split_names(raw)
    if not names:
        raise Refused("--repos is empty")
    for name in names:
        if name not in seg.repos and not has_profile(name):
            raise Refused(f"repo {name!r} was not touched in this segment and has no vault profile")
    return names


def pick(seg: Segment, linked: list[str], tickets: list[str], repos: list[str]) -> routing.Target:
    """Card target: the routed one, with [[KEY]] links counted and --tickets/--repos applied."""
    fresh = [k for k in linked if k not in seg.tickets]
    if fresh:
        seg.apply({"ev": "keys", "keys": fresh})
    target = routing.handoff_targets(seg)[0] if fresh else routing.card_targets(seg)[0]
    if tickets:
        key = max(tickets, key=seg.ticket_rank)
        target = dict(target, key=key, path=f"handoffs/{fold.safe_name(key)}.md", tickets=tickets)
    if repos:
        target = dict(target, repos=repos)
        if target["key"] == "-" and not seg.name_slug:
            target["path"] = f"handoffs/{fold.safe_name(repos[0])}.md"
    return target
