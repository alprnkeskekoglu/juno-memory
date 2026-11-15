import re

SECTION_RE = re.compile(r"^[A-Z][A-Za-z0-9 -]{1,29}$")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
RESERVED = frozenset({"notes", "decisions", "links", "current", "history"})
FIELD = "digests"


def blocks(doc: str, doc_title: str) -> list[tuple[str, str]]:
    """Digest blocks of a ticket file for its pack, as (title, body); none until P4."""
    return []
