import math
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any

TOKEN_RE = re.compile(r"[^\W_]{3,}")
K1 = 1.2
B = 0.75


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.casefold())


QUERY_KEYS = ("file_path", "path", "notebook_path", "command", "pattern")
QUERY_CHARS = 600


def query_text(prompt: str, tool_in: Mapping[str, Any], cwd: str = "") -> str:
    """The last user prompt plus the tool input that names a file or a command."""
    base = cwd.rstrip("/") + "/" if cwd else ""
    parts = [prompt]
    for key in QUERY_KEYS:
        value = tool_in.get(key)
        if isinstance(value, str):
            parts.append(value.replace(base, "") if base else value)
    return "\n".join(p for p in parts if p.strip())[-QUERY_CHARS:]


def bm25(items: list[str], query: str) -> list[float]:
    """Okapi BM25 of each item against the query, the items being the corpus."""
    wanted = set(tokens(query))
    docs = [Counter(tokens(item)) for item in items]
    if not wanted or not docs:
        return [0.0] * len(items)
    average = sum(sum(d.values()) for d in docs) / len(docs) or 1.0
    scores: list[float] = []
    for doc in docs:
        size = sum(doc.values())
        score = 0.0
        for term in wanted & doc.keys():
            have = sum(1 for d in docs if term in d)
            idf = math.log(1 + (len(docs) - have + 0.5) / (have + 0.5))
            freq = doc[term]
            score += idf * freq * (K1 + 1) / (freq + K1 * (1 - B + B * size / average))
        scores.append(score)
    return scores


def scale(values: list[float]) -> list[float]:
    low, high = min(values), max(values)
    return [(v - low) / (high - low) if high > low else 0.0 for v in values]


def ranked_indices(items: list[str], room: int, query: str) -> list[int] | None:
    """Item indexes to keep, in display order: items are taken by an equal-weight sum of
    min-max scaled relevance and recency (position) while they fit. None without a usable query."""
    relevance = scale(bm25(items, query))
    if not any(relevance):
        return None
    recency = scale([float(i) for i in range(len(items))])
    order = sorted(range(len(items)), key=lambda i: (-(relevance[i] + recency[i]), -i))
    kept: list[int] = []
    used = -1
    for i in order:
        if used + 1 + len(items[i]) <= room:
            kept.append(i)
            used += 1 + len(items[i])
    return sorted(kept)


RELEVANCE_MIN = 0.25
RELEVANCE_WORD = 4


def related(item: str, query: str, skip: frozenset[str] = frozenset()) -> float:
    """BM25 of the query against one item, without the `skip` words and words under four
    characters: what the query shares with the item beyond the word that matched it. One shared
    word scores about 0.29 (the corpus is the item alone), so the minimum is one shared word."""
    words = {w for w in tokens(query) if len(w) >= RELEVANCE_WORD and w not in skip}
    return bm25([item], " ".join(sorted(words)))[0]
