import time
from collections.abc import Sequence
from typing import Any

from juno.core import fold, privacy
from juno.core.ledger import Segment
from juno.core.redact import redact

Target = dict[str, Any]


def local_stamp(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() if now is None else now))


def handoff_targets(seg: Segment) -> list[Target]:
    best = seg.current_ticket()
    tickets = list(seg.tickets)
    if best:
        return [
            {
                "path": f"handoffs/{fold.safe_name(best)}.md",
                "key": best,
                "repos": list(seg.repos),
                "tickets": list(dict.fromkeys([*tickets, best])),
            }
        ]
    if seg.repos and not seg.name_slug:
        return [
            {
                "path": f"handoffs/{fold.safe_name(repo)}.md",
                "key": "-",
                "repos": [repo],
                "tickets": tickets,
            }
            for repo in seg.repos
        ]
    return [
        {
            "path": f"handoffs/{seg.name_slug or 'misc'}.md",
            "key": "-",
            "repos": list(seg.repos),
            "tickets": tickets,
        }
    ]


def card_targets(seg: Segment) -> list[Target]:
    targets = handoff_targets(seg)
    return [dict(targets[0], path=p) for p in seg.card_files] or targets


def stub_card(
    seg: Segment,
    target: Target,
    now: float,
    excerpt_chars: int,
    prefixes: Sequence[str] | None = None,
    failure: str | None = None,
) -> str:
    excerpt = " ".join(redact(seg.last_msg).split())[:excerpt_chars]
    if not excerpt or privacy.scan(excerpt, prefixes):
        excerpt = "-"
    lead = f"Card job failed ({failure})" if failure else "Segment ended without a card"
    return (
        "\n".join(
            [
                f"## {local_stamp(now)} · {target['key']} · stub · {seg.sid[:8]}",
                "auto: true",
                "Repos: " + (", ".join(target["repos"]) or "-"),
                "Tickets: " + (", ".join(target["tickets"]) or "-"),
                f"{lead}; Juno wrote this stub "
                f"({seg.tools} tool calls, {seg.turns} user turns, {seg.edit_total} edits).",
                "Last assistant message: " + excerpt,
                "Learned: none",
            ]
        )
        + "\n"
    )


def previous_segment_line(
    seg: Segment, paths: list[str], now: float | None = None, failure: str | None = None
) -> str:
    why = f"; its card job failed ({failure}) and the card was dropped" if failure else ""
    return (
        f"Juno previous segment {seg.sid[:8]} ended without a current card{why} "
        f"(repos: {', '.join(seg.repos) or '-'}; tickets: {', '.join(seg.tickets) or '-'}). "
        f"Its stub card in {', '.join(paths)} is improved by the stub pass; vault Edit and Write "
        'are refused. `juno card --label L --learned "..." "<done>" "<next>"` writes only '
        "this session's own card, not the previous segment's."
    )
