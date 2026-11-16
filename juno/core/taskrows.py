from dataclasses import dataclass

from juno.core import taskdigest, taskfile


@dataclass(frozen=True)
class Row:
    section: str
    key: str
    text: str


def apply(text: str, key: str, rows: list[Row], day: str) -> tuple[str, int, int]:
    """Put each row into a task file text; returns (new text, lines added, lines replaced)."""
    digests = taskdigest.listed(text)
    added = replaced = 0
    for row in rows:
        line = taskdigest.item_line(row.key, row.text, day)
        text, how = taskdigest.put_line(text, digests, row.section, row.key, line)
        added += how == "added"
        replaced += how == "replaced"
        if row.section not in digests:
            digests.append(row.section)
    text = taskfile.merge(text, key, {}, day)
    if digests != taskdigest.listed(text):
        text = taskdigest.set_digests(text, digests)
    return text, added, replaced
