"""Plan the move of `decisions/<x>.md` into the file that owns the decisions (#31).

Pure: texts in, texts out. A profile or ticket owns its decisions in a `## Decisions` section; the
legacy file's `## Current` lines are merged into it (exact duplicates only, so a pack shows the
same lines before and after) and its `## History` lines go to `archive/decisions/<topic>.md`.
"""

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from juno.core import owners, taskfile, vaultfmt
from juno.core.detect import Tickets
from juno.core.errors import Refused

DIR = "decisions/"
KEPT = ("Current", "History")
PATH_LINK_RE = re.compile(r"\[\[decisions/([^\]|#]+)")
SECTION_RE = re.compile(r"^##\s+(.*?)\s*$")


@dataclass(frozen=True)
class Move:
    source: str
    owner: owners.Owner
    owner_text: str  # the whole owner file after this move
    created: bool  # the owner file does not exist yet
    archive_rel: str
    archive_text: str | None  # None when the file has no History line
    lines: int
    history: int


@dataclass(frozen=True)
class Skip:
    source: str
    reason: str


@dataclass
class Plan:
    moves: list[Move] = field(default_factory=list)
    skips: list[Skip] = field(default_factory=list)


def stray_body(body: str) -> bool:
    """True when the body holds anything besides blank lines and one `## Current`/`## History`."""
    seen: list[str] = []
    inside = False
    for line in body.split("\n"):
        heading = SECTION_RE.match(line)
        if heading and heading.group(1) in KEPT:
            if heading.group(1) in seen:
                return True
            seen.append(heading.group(1))
            inside = True
        elif re.match(r"^#{1,2}\s", line) or (line.strip() and not inside):
            return True
    return False


def owner_of(rel: str, profiles: Collection[str], tickets: Tickets) -> owners.Owner | None:
    """The profile or ticket that owns decisions/<x>.md, None for an ownerless topic."""
    try:
        found = owners.decision_owner(rel[len(DIR) : -len(".md")], profiles, tickets)
    except Refused:
        return None
    if found.kind == "topic":
        # a profile reads decisions/<name>.md and decisions/<NAME>.md (the old uppercasing bug)
        for name in sorted(profiles):
            if rel in owners.legacy_decision_rels("profile", name):
                return owners.decision_owner(name, profiles, tickets)
        return None
    return found


def plan(
    decisions: Mapping[str, str],
    profiles: Collection[str],
    tickets: Tickets,
    owner_texts: Mapping[str, str],
    archive_texts: Mapping[str, str],
    hidden: Mapping[str, str],
    today: str,
    base_url: str = "",
) -> Plan:
    """`decisions` maps `decisions/<x>.md` to its text; `owner_texts` and `archive_texts` map the
    files that exist (missing = absent); `hidden` maps a hidden file to the reason it is hidden."""
    result = Plan()
    owner_now = dict(owner_texts)
    archive_now = dict(archive_texts)
    for rel in sorted(decisions):
        found = owner_of(rel, profiles, tickets)
        if found is None:
            continue
        text = decisions[rel]
        _, body = vaultfmt.parse_frontmatter(text)
        if rel in hidden:
            result.skips.append(Skip(rel, f"the file is hidden ({hidden[rel]})"))
            continue
        if found.rel in hidden:
            result.skips.append(Skip(rel, f"{found.rel} is hidden ({hidden[found.rel]})"))
            continue
        if stray_body(body):
            result.skips.append(Skip(rel, "it has text outside ## Current and ## History"))
            continue
        created = found.rel not in owner_now
        if created:
            url = taskfile.default_url(base_url, found.topic) if found.kind == "ticket" else ""
            base = taskfile.stub(found.topic, today, url or None)
        else:
            base = owner_now[found.rel]
        base = owners.ensure_section(base, "Decisions", first=True)
        current = vaultfmt.section(body, "Current")
        merged = owners.merged_decisions(vaultfmt.section(base, "Decisions"), current)
        owner_now[found.rel] = owners.set_section_lines(base, "Decisions", merged)
        history = [x for x in vaultfmt.section(body, "History").splitlines() if x.strip()]
        archive_rel = owners.archive_rel(found.topic)
        archive_text = None
        if history:
            archive_text = owners.archive_append(
                archive_now.get(archive_rel, ""), found.topic, history
            )
            archive_now[archive_rel] = archive_text
        result.moves.append(
            Move(
                rel,
                found,
                owner_now[found.rel],
                created,
                archive_rel,
                archive_text,
                len([x for x in current.splitlines() if x.strip()]),
                len(history),
            )
        )
    return result


def count_path_links(texts: Mapping[str, str], moved: Collection[str]) -> int:
    """Explicit `[[decisions/<x>]]` links that point at a file the move removes."""
    gone = {rel[len(DIR) : -len(".md")].lower() for rel in moved}
    total = 0
    for rel, text in texts.items():
        if rel in moved:
            continue
        for match in PATH_LINK_RE.finditer(text):
            name = match.group(1).strip().removesuffix(".md").lower()
            total += name in gone
    return total
