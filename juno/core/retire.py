import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from juno.core import vaultfmt

ARCHIVED_REASON = "status: archived"


@dataclass(frozen=True)
class FileMeta:
    status: str = ""
    supersedes: tuple[tuple[str, str], ...] = ()


@dataclass
class Resolution:
    retired: dict[str, str] = field(default_factory=dict)
    pairs: list[tuple[str, str]] = field(default_factory=list)
    dead: list[dict[str, object]] = field(default_factory=list)

    def entries(self) -> list[dict[str, object]]:
        by_target: dict[str, str] = {}
        for target, source in self.pairs:
            by_target.setdefault(target, source)
        found: list[dict[str, object]] = []
        for rel, why in sorted(self.retired.items()):
            if why == ARCHIVED_REASON:
                found.append({"path": rel, "by": None, "reason": "archived"})
            else:
                found.append({"path": rel, "by": by_target.get(rel), "reason": "supersedes"})
        return found


def supersedes_refs(fields: vaultfmt.Fields) -> tuple[tuple[str, str], ...]:
    refs: list[tuple[str, str]] = []
    for item in vaultfmt.field_items(fields, "supersedes"):
        links = vaultfmt.WIKILINK_RE.findall(item)
        if links:
            refs.extend((x, "link") for x in links)
        else:
            refs.append((item, "path"))
    return tuple(refs)


def meta_of(text: str) -> FileMeta:
    fields, _ = vaultfmt.parse_frontmatter(text)
    return FileMeta(vaultfmt.field_str(fields, "status"), supersedes_refs(fields))


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def cycle_edges(edges: Iterable[tuple[str, str]]) -> set[tuple[str, str]]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    edge_list = list(edges)
    for src, tgt in edge_list:
        adjacency[src].append(tgt)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    comp: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    ncomp = 0
    for root in list(adjacency):
        if root in index:
            continue
        work = [(root, iter(adjacency[root]))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(adjacency.get(nxt, ()))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                while True:
                    popped = stack.pop()
                    on_stack.discard(popped)
                    comp[popped] = ncomp
                    if popped == node:
                        break
                ncomp += 1
    return {(s, t) for s, t in edge_list if s in comp and comp.get(s) == comp.get(t)}


def normalise_ref(raw: str, kind: str) -> str:
    ref = (
        raw.split("|", 1)[0].split("#", 1)[0].strip().replace("\\", "/")
        if kind == "link"
        else raw.strip()
    )
    ref = nfc(ref)
    ref = ref[2:] if ref.startswith("./") else ref.lstrip("/")
    return ref[:-3] if ref.endswith(".md") else ref


def resolve(files: Mapping[str, FileMeta]) -> Resolution:
    result = Resolution()
    by_noext: dict[str, list[str]] = {}
    by_stem: dict[str, list[str]] = {}
    for rel in sorted(files):
        noext = nfc(rel[:-3] if rel.endswith(".md") else rel)
        by_noext.setdefault(noext, []).append(rel)
        by_stem.setdefault(noext.rsplit("/", 1)[-1], []).append(rel)
    archived = {rel for rel, meta in files.items() if meta.status == "archived"}

    def dead(src: str, raw: str, reason: str, cands: Iterable[str]) -> None:
        result.dead.append({"path": src, "ref": raw, "reason": reason, "candidates": sorted(cands)})

    resolved: list[tuple[str, str, str]] = []
    for src in sorted(files):
        for raw, kind in files[src].supersedes:
            ref = normalise_ref(raw, kind)
            parts = ref.split("/")
            candidates: list[str] = []
            if ref and ".." not in parts and "" not in parts:
                if ref in by_noext:
                    candidates = by_noext[ref]
                elif kind == "link" and "/" not in ref:
                    candidates = by_stem.get(ref, [])
            candidates = [c for c in candidates if c != src]
            if len(candidates) != 1:
                dead(src, raw, "ambiguous" if candidates else "unresolved", candidates)
            elif vaultfmt.top_of(candidates[0]) in vaultfmt.PROTECTED_TOPS:
                dead(src, raw, "protected", candidates)
            else:
                resolved.append((src, candidates[0], raw))

    cyc = cycle_edges({(s, t) for s, t, _ in resolved})
    edges: list[tuple[str, str, str]] = []
    for src, tgt, raw in resolved:
        if (src, tgt) in cyc:
            dead(src, raw, "cycle", [tgt])
        else:
            edges.append((src, tgt, raw))

    successors: dict[str, list[tuple[str, str]]] = defaultdict(list)
    indegree: Counter[str] = Counter()
    nodes: set[str] = set()
    for src, tgt, raw in edges:
        successors[src].append((tgt, raw))
        indegree[tgt] += 1
        nodes.update((src, tgt))
    order = [n for n in sorted(nodes) if indegree[n] == 0]
    retired = dict.fromkeys(archived, ARCHIVED_REASON)
    sources: dict[str, list[str]] = defaultdict(list)
    i = 0
    while i < len(order):
        node = order[i]
        i += 1
        active = node not in archived and node not in sources
        for tgt, raw in successors.get(node, ()):
            if active:
                retired.setdefault(tgt, f"superseded by {node}")
                if node not in sources[tgt]:
                    sources[tgt].append(node)
            else:
                dead(node, raw, "inactive-source", [tgt])
            indegree[tgt] -= 1
            if indegree[tgt] == 0:
                order.append(tgt)
    result.retired = retired
    result.pairs = sorted((tgt, src) for tgt, srcs in sources.items() for src in srcs)
    return result
