import re
import unicodedata
from collections import Counter
from collections.abc import Collection, Mapping
from typing import Any

from juno.core import vaultfmt

CACHE_VERSION = 1
MAX_TITLE = 80
MAX_TARGET = 200
SHOWN_TARGET = 60
LINKED_ENTRIES = 5
LINKED_CHARS = 500
SKIPPED_DIRS = ("handoffs/", "archive/")
PREFERRED_DIRS = ("decisions/", "knowledge/", "procedures/", "tasks/", "projects/")
LINK_RE = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")
H1_RE = re.compile(r"^#[ \t]+(.+?)[ \t#]*$")

LinkIndex = dict[str, Any]
Files = Mapping[str, Any]


def link_target(raw: str) -> str:
    return raw.split("|", 1)[0].split("#", 1)[0].rstrip("\\").strip()


def body_start(lines: list[str]) -> int:
    if not lines or lines[0].rstrip() != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i].rstrip() in ("---", "..."):
            return i + 1
    return 0


def wikilinks(text: str | None) -> list[tuple[int, str]]:
    links: list[tuple[int, str]] = []
    fence: str | None = None
    lines = (text or "").split("\n")
    start = body_start(lines)
    for number, line in enumerate(lines[start:], start + 1):
        match = FENCE_RE.match(line)
        if match:
            mark = match.group(1)
            if fence is None:
                fence = mark
            elif (
                mark[0] == fence[0]
                and len(mark) >= len(fence)
                and not line.strip()[len(mark) :].strip()
            ):
                fence = None
            continue
        if fence is not None:
            continue
        for raw in LINK_RE.findall(INLINE_CODE_RE.sub(" ", line)):
            target = link_target(raw)
            if target:
                links.append((number, target[: MAX_TARGET + 1]))
    return links


def key_of(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def build_index(relpaths: Collection[str]) -> LinkIndex:
    paths: dict[str, str] = {}
    stems: dict[str, list[str]] = {}
    for relpath in relpaths:
        noext = key_of(relpath[:-3] if relpath.endswith(".md") else relpath)
        paths[noext] = relpath
        stems.setdefault(noext.rsplit("/", 1)[-1], []).append(relpath)
    for names in stems.values():
        names.sort(key=lambda r: (r.count("/"), len(r), r))
    return {"paths": paths, "stems": stems}


def resolve_link(target: str, index: LinkIndex) -> str | None:
    if len(target) > MAX_TARGET:
        return None
    ref = key_of(target.replace("\\", "/").strip())
    ref = ref[2:] if ref.startswith("./") else ref.lstrip("/")
    if ref.endswith(".md"):
        ref = ref[:-3]
    if not ref:
        return None
    if ref in index["paths"]:
        found: str = index["paths"][ref]
        return found
    candidates: list[str] = index["stems"].get(ref.rsplit("/", 1)[-1], [])
    if "/" in ref:
        candidates = [c for c in candidates if key_of(c[:-3]).endswith("/" + ref)]
    return candidates[0] if candidates else None


def title_of(relpath: str, text: str) -> str:
    fields, body = vaultfmt.parse_frontmatter(text)
    raw = fields.get("title")
    title = raw.strip().strip("'\"") if isinstance(raw, str) else ""
    if not title:
        title = next((m.group(1) for m in map(H1_RE.match, body.split("\n")) if m), "")
    if not title:
        idea = vaultfmt.section(text, "Idea")
        title = idea.split("\n", 1)[0].strip() if idea else ""
    title = " ".join((title or relpath.rsplit("/", 1)[-1][:-3]).split())
    return title if len(title) <= MAX_TITLE else title[: MAX_TITLE - 1].rstrip() + "…"


def parse_file(relpath: str, text: str, sig: list[int] | None) -> dict[str, Any]:
    fields, _ = vaultfmt.parse_frontmatter(text)
    hidden = vaultfmt.is_archived(fields) or vaultfmt.is_private(fields)
    return {
        "sig": sig,
        "title": title_of(relpath, text),
        "hidden": hidden,
        "links": [list(link) for link in wikilinks(text)],
    }


def valid_link(link: object) -> bool:
    return (
        isinstance(link, list)
        and len(link) == 2
        and type(link[0]) is int
        and isinstance(link[1], str)
        and 0 < len(link[1]) <= MAX_TARGET + 1
    )


def valid_entry(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("title"), str)
        and isinstance(entry.get("hidden"), bool)
        and isinstance(entry.get("links"), list)
        and all(valid_link(link) for link in entry["links"])
    )


def anchor_of(kind: str, key: str, safe_key: str) -> str:
    return f"tasks/{key}.md" if kind == "ticket" else f"projects/{safe_key}.md"


def neighbours(anchor: str, files: Files) -> list[tuple[str, str]]:
    index = build_index(list(files))
    outgoing = [resolve_link(t, index) for _, t in files[anchor]["links"]]
    backlinks = sorted(
        r
        for r, e in files.items()
        if r != anchor and any(resolve_link(t, index) == anchor for _, t in e["links"])
    )
    found = [("→", r) for r in dict.fromkeys(r for r in outgoing if r and r != anchor)]
    seen = {r for _, r in found}
    return found + [("←", r) for r in backlinks if r not in seen]


def dir_rank(relpath: str) -> int:
    for rank, prefix in enumerate(PREFERRED_DIRS):
        if relpath.startswith(prefix):
            return rank
    return len(PREFERRED_DIRS)


def functional_neighbours(
    anchor: str, files: Files, retired: Collection[str], exclude: Collection[str] = ()
) -> list[tuple[str, str]]:
    kept = [
        (arrow, relpath)
        for arrow, relpath in neighbours(anchor, files)
        if relpath not in retired
        and relpath not in exclude
        and not files[relpath]["hidden"]
        and not relpath.startswith(SKIPPED_DIRS)
    ]
    return sorted(kept, key=lambda pair: dir_rank(pair[1]))


def linked_paths(
    anchor: str, files: Files, retired: Collection[str], exclude: Collection[str] = ()
) -> list[str]:
    return [r for _, r in functional_neighbours(anchor, files, retired, exclude)][:LINKED_ENTRIES]


def linked_lines(
    anchor: str,
    files: Files,
    retired: Collection[str],
    mark: Any,
    neutralize: Any,
    exclude: Collection[str] = (),
) -> str:
    picked: list[str] = []
    used = 0
    for arrow, relpath in functional_neighbours(anchor, files, retired, exclude):
        title = neutralize(" ".join(files[relpath]["title"].split()))
        line = f"- {arrow} {title} · {' '.join(relpath.splitlines())}{mark(relpath)}"
        if len(picked) >= LINKED_ENTRIES or used + len(line) + 1 > LINKED_CHARS:
            break
        picked.append(line)
        used += len(line) + 1
    return "\n".join(picked)


def shown_target(target: str) -> str:
    return target if len(target) <= MAX_TARGET else target[:SHOWN_TARGET] + "…"


def broken_links(files: Files) -> list[tuple[str, int, str]]:
    index = build_index(list(files))
    return [
        (relpath, number, target)
        for relpath in sorted(files)
        for number, target in files[relpath]["links"]
        if resolve_link(target, index) is None
    ]


LINK_REPORT_LINES = 20


def link_report(files: Files) -> list[tuple[str, str]]:
    broken = broken_links(files)
    total = sum(len(e["links"]) for e in files.values())
    if not broken:
        return [("ok", f"{total} wikilinks in {len(files)} files resolve")]
    report = [
        ("warn", f"broken link {relpath}:{number} → [[{shown_target(target)}]]")
        for relpath, number, target in broken[:LINK_REPORT_LINES]
    ]
    if len(broken) > LINK_REPORT_LINES:
        report.append(("warn", f"{len(broken) - LINK_REPORT_LINES} more broken links not shown"))
    ghosts = Counter(shown_target(target) for _, _, target in broken)
    ranked = sorted(ghosts.items(), key=lambda item: (-item[1], item[0]))
    shown = ", ".join(f"{name} ({count})" for name, count in ranked[:LINK_REPORT_LINES])
    more = f" and {len(ranked) - LINK_REPORT_LINES} more" if len(ranked) > LINK_REPORT_LINES else ""
    report.append(("warn", f"{len(ranked)} ghost link targets: {shown}{more}"))
    return report
