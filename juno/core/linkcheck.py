from collections import Counter
from collections.abc import Mapping

from juno.core import links
from juno.core.vaultfmt import is_archived, is_private, kind_of, parse_frontmatter

Check = tuple[str, str, str]
LINK_SHOWN = 20

SHARED_LINKS = "%d bare links name a stem with more than one file (a path link is exact)"


ORPHAN_KINDS = frozenset({"decision", "knowledge", "procedure", "project", "note", "idea"})


def link_checks(files: Mapping[str, str]) -> list[Check]:
    """Wikilinks and local Markdown links (see links.refs): missing files WARN with a ghost
    summary, missing #headings WARN separately, and INFO counts (never names) for links into
    private or archived notes and for notes nothing links to."""
    by_rel = {r[:-3].casefold(): r for r in files}
    by_md = {r.casefold(): r for r in files}
    stems: dict[str, list[str]] = {}
    for low, rel in by_rel.items():
        stems.setdefault(low.rsplit("/", 1)[-1], []).append(rel)
    for names in stems.values():
        names.sort(key=links.bare_rank)
    hidden: set[str] = set()
    for rel, text in files.items():
        fields = parse_frontmatter(text)[0]
        if is_archived(fields) or is_private(fields):
            hidden.add(rel)
    heads: dict[str, links.Headings] = {}
    broken: list[tuple[str, int, str]] = []
    bad_heads: list[tuple[str, int, str]] = []
    wikis = mds = shared = to_hidden = 0
    linked_in: set[str] = set()
    for rel, text in sorted(files.items()):
        for ref in links.refs(rel, text):
            wiki = ref.kind == "wiki"
            if wiki and not ref.path:
                found = [rel]
            elif wiki:
                low = ref.path.casefold().removesuffix(".md")
                if "/" in low:
                    found = [by_rel[low]] if low in by_rel else []
                else:
                    found = stems.get(low, [])
            else:
                found = [by_md[ref.path.casefold()]] if ref.path.casefold() in by_md else []
            if wiki:
                wikis += bool(ref.path)
            else:
                mds += 1
            if not found:
                shown = f"[[{ref.path}]]" if wiki else f"({ref.shown.partition('#')[0]})"
                broken.append((rel, ref.line, shown))
                continue
            if wiki and ref.path and "/" not in ref.path and len(found) > 1:
                shared += 1
            linked_in.update(r for r in found if r != rel)
            if found[0] in hidden:
                to_hidden += rel not in hidden
                continue
            if ref.frag:
                for target in found:
                    if target not in heads:
                        heads[target] = links.headings(files[target])
                if not any(links.has_heading(ref.frag, heads[t], not wiki) for t in found):
                    shown = f"[[{ref.shown}]]" if wiki else f"({ref.shown})"
                    bad_heads.append((rel, ref.line, shown))
    out: list[Check] = [
        ("WARN", "links", f"broken link {r}:{n} -> {t}") for r, n, t in broken[:LINK_SHOWN]
    ]
    if len(broken) > LINK_SHOWN:
        out.append(("WARN", "links", f"{len(broken) - LINK_SHOWN} more broken links not shown"))
    if broken:
        ghosts = Counter(t for _, _, t in broken)
        ranked = sorted(ghosts.items(), key=lambda item: (-item[1], item[0]))[:LINK_SHOWN]
        said = ", ".join(f"{t} ({n})" for t, n in ranked)
        out.append(("WARN", "links", f"{len(ghosts)} ghost link targets: {said}"))
    else:
        said = f"{wikis} wikilinks and {mds} markdown links in {len(files)} files resolve"
        out.append(("PASS", "links", said))
    out += [
        ("WARN", "links", f"missing heading {r}:{n} -> {t}") for r, n, t in bad_heads[:LINK_SHOWN]
    ]
    if len(bad_heads) > LINK_SHOWN:
        out.append(
            ("WARN", "links", f"{len(bad_heads) - LINK_SHOWN} more missing headings not shown")
        )
    if shared:
        out.append(("INFO", "links", SHARED_LINKS % shared))
    if to_hidden:
        said = (
            f"{to_hidden} links from visible notes point to private or archived notes (not listed)"
        )
        out.append(("INFO", "links", said))
    orphans = sum(
        1
        for rel in files
        if kind_of(rel) in ORPHAN_KINDS and rel not in hidden and rel not in linked_in
    )
    if orphans:
        said = f"{orphans} notes are not linked from any other file "
        out.append(
            ("INFO", "links", said + "(decisions, knowledge, procedures, projects, notes, ideas)")
        )
    return out
