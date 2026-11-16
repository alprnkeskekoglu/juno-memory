"""The relevance gate for topic rules and knowledge (#36): a plain word alone is not enough."""

from juno.core import rank, topics, vaultfmt
from juno.core.view import VaultView

ITEM_CHARS = 6000


def plain(trigger: str) -> bool:
    """A single word of letters: generic by shape. A flag, path, dotted, hyphenated, underscored
    or digit term, and any phrase of two words or more, is specific enough alone."""
    return trigger.isalpha()


def item_text(view: VaultView, entry: topics.Entry) -> str:
    """What the item says: rule texts, or a knowledge file's title, summary and body, and its
    triggers (another language's form of the word counts as a shared word)."""
    name, relpath = entry[0], entry[1]
    text = view.read(relpath)
    if name.startswith("topic-"):
        body = " ".join(vaultfmt.rule_texts(text)) + " " + name[len("topic-") :].replace("-", " ")
    else:
        fields, rest = vaultfmt.parse_frontmatter(text)
        body = " ".join([str(fields.get("title") or ""), str(fields.get("summary") or ""), rest])
    return str(body + " " + " ".join(entry[2]))[:ITEM_CHARS]


def judge(view: VaultView) -> topics.Judge:
    def decide(entry: topics.Entry, found: list[str], haystack: str) -> bool:
        if entry[0].startswith(topics.PROCEDURE_PREFIX) or not all(plain(t) for t in found):
            return True
        skip = frozenset(word for trigger in found for word in rank.tokens(trigger))
        return rank.related(item_text(view, entry), haystack, skip) >= rank.RELEVANCE_MIN

    return decide
