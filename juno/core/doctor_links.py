from collections.abc import Mapping

from juno.core import links
from juno.core.cardfmt import LINK_RE, PAREN_REASON_RE, REASON_RE

Check = tuple[str, str, str]
LINKED_TOPS = ("notes/", "knowledge/")


def reasonless_card_links(text: str) -> int:
    count = 0
    for line in text.splitlines():
        if not line.startswith("Links:"):
            continue
        for link in LINK_RE.finditer(line):
            reason = PAREN_REASON_RE.match(line[link.end() :])
            count += not (reason and REASON_RE.search(reason.group(1)))
    return count


def info_checks(files: Mapping[str, str], handoffs: Mapping[str, str]) -> list[Check]:
    """Counts only (INFO): handoff card links with no reason, and notes or knowledge files that
    no file links to and that link to nothing."""
    out: list[Check] = []
    bare = sum(reasonless_card_links(text) for text in handoffs.values())
    if bare:
        out.append(("INFO", "links", f"{bare} handoff card links have no reason"))
    sources = {**files, **handoffs}
    index = links.build_index(list(sources))
    linked_in = {
        resolved
        for rel, text in sources.items()
        for _, target in links.wikilinks(text)
        if (resolved := links.resolve_link(target, index)) and resolved != rel
    }
    alone = sum(
        1
        for rel, text in files.items()
        if rel.startswith(LINKED_TOPS) and rel not in linked_in and not links.wikilinks(text)
    )
    if alone:
        out.append(("INFO", "links", f"{alone} note or knowledge files have no link in or out"))
    return out
