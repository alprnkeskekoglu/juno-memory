import re
from collections.abc import Callable

from juno.core import taskdigest
from juno.core.errors import Refused
from juno.core.ledger import Segment
from juno.core.taskrows import Row
from juno.core.textchecks import text_field

MAX_TICKETS = 3
MAX_LINES = 20
SECTIONS = ("Summary", "Criteria", "Work", "Tests", "Cause")
TEXT_CHARS = (8, 1000)
EVIDENCE_CHARS = (4, 200)
KEYS = {
    "Summary": re.compile(r"summary"),
    "Criteria": re.compile(r"ac[1-9][0-9]{0,2}"),
    "Work": re.compile(r"[a-z0-9][a-z0-9._-]{0,39}"),
    "Tests": re.compile(r"outcome|case-[a-z0-9][a-z0-9._-]{0,32}"),
    "Cause": re.compile(r"cause"),
}
PR_LINE_RE = re.compile(r"^- PR https://", re.M)
URL_RE = re.compile(r"https?://\S+")
PATH_RE = re.compile(r"(?<![\w/.:-])(?:~/|/)[\w.@+-]+")
BARE_RE = re.compile(r"\d+(?:\s*(?:of|/)\s*\d+)?(?:\s+[A-Za-z]{1,12}){0,2}")


def worked(seg: Segment, pr: bool) -> list[str]:
    """The ticket keys of a segment that were worked on, not only named: a ledger ticket with an
    edit on a branch naming it, a main-session tool call in a repo on such a branch or a tracker
    read of it, or a PR URL together with a typed prompt naming it. A typed mention alone is not
    enough. Highest rank first, at most `MAX_TICKETS`."""
    found = [
        key
        for key in seg.tickets
        if seg.ticket_edits[key] or seg.main_tickets[key] or (pr and seg.typed_tickets[key])
    ]
    return sorted(found, key=seg.ticket_rank, reverse=True)[:MAX_TICKETS]


def has_pr(digest_text: str) -> bool:
    return PR_LINE_RE.search(digest_text) is not None


def check_line(raw: object, vault: str) -> tuple[str | None, Row | None, str]:
    """Shape checks of one proposed line: (problem, row, evidence). The privacy scan and the
    anchoring in the digest run in the caller."""
    if not isinstance(raw, dict):
        return "invalid", None, ""
    section, key = text_field(raw.get("section")), text_field(raw.get("key"))
    text, evidence = text_field(raw.get("text")).replace("·", "-"), text_field(raw.get("evidence"))
    if section not in SECTIONS:
        return "section", None, ""
    key = key.casefold() if section == "Work" else key
    if not taskdigest.KEY_RE.fullmatch(key) or not KEYS[section].fullmatch(key):
        return "key", None, ""
    if PATH_RE.search(URL_RE.sub("", text.replace(vault, "") if vault else text)):
        return "path", None, ""
    if BARE_RE.fullmatch(text) or len(text.split()) < 3:
        return "bare", None, ""
    if not TEXT_CHARS[0] <= len(text) <= TEXT_CHARS[1]:
        return "invalid", None, ""
    if not EVIDENCE_CHARS[0] <= len(evidence) <= EVIDENCE_CHARS[1]:
        return "no evidence", None, ""
    try:
        taskdigest.check_text(text)
    except Refused:
        return "invalid", None, ""
    return None, Row(section, key, text), evidence


def check_all(
    lines: list[object], vault: str, accept: Callable[[str, str], str | None]
) -> tuple[list[Row], dict[str, int]]:
    """Rows to write and dropped-reason counts. `accept(text, evidence)` is the adapter's
    anchoring and privacy check: a reason to drop the line, or None."""
    rows: list[Row] = []
    dropped: dict[str, int] = {}
    for raw in lines[:MAX_LINES]:
        problem, row, evidence = check_line(raw, vault)
        if row is not None:
            problem = accept(row.text, evidence)
        if problem or row is None:
            reason = problem or "invalid"
            dropped[reason] = dropped.get(reason, 0) + 1
        else:
            rows.append(row)
    return rows, dropped
