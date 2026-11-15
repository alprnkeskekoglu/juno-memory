"""Pure helpers for `juno consolidate` and `juno fix --merge`: sections, near-duplicate groups."""

import re

from juno.core import pitfalldup, writegate
from juno.core.errors import Refused

MERGE_RE = re.compile(r"^(?P<rel>[^:\n]+\.md):(?P<nums>[1-9][0-9]{0,5}(?:,[1-9][0-9]{0,5})+)$")


def section_bounds(lines: list[str], name: str | None) -> tuple[int, int]:
    """0-based [start, end) of the lines under `## name`, or of the whole file when no name."""
    if name is None:
        return 0, len(lines)
    at = next((i for i, x in enumerate(lines) if x.strip() == f"## {name}"), None)
    if at is None:
        raise Refused(f"no '## {name}' section in that file")
    end = next((i for i in range(at + 1, len(lines)) if re.match(r"#{1,2}\s", lines[i])), None)
    return at + 1, len(lines) if end is None else end


def section_of(lines: list[str], index: int) -> str:
    """The nearest heading line above lines[index] ('' before any heading)."""
    for i in range(index, -1, -1):
        if re.match(r"#{1,6}\s", lines[i]):
            return lines[i].strip()
    return ""


def near_groups(items: list[tuple[int, str]], preset: str) -> list[list[int]]:
    """Groups (size 2+) of line numbers whose texts pitfalldup counts as the same fact."""
    parent = {n: n for n, _ in items}

    def root(n: int) -> int:
        while parent[n] != n:
            n = parent[n]
        return n

    for i, (a, x) in enumerate(items):
        for b, y in items[i + 1 :]:
            if pitfalldup.near_text(writegate.core_text(x), writegate.core_text(y), preset):
                parent[root(b)] = root(a)
    found: dict[int, list[int]] = {}
    for n, _ in items:
        found.setdefault(root(n), []).append(n)
    return [g for g in found.values() if len(g) > 1]


def split_merge_ref(ref: str) -> tuple[str, list[int]]:
    match = MERGE_RE.match(ref.strip())
    if not match:
        raise Refused("--merge must look like <vault-relative file>.md:<line>,<line>[,<line>...]")
    rel = match["rel"].replace("\\", "/").lstrip("/")
    if ".." in rel.split("/"):
        raise Refused("ref must stay inside the vault")
    numbers = [int(x) for x in match["nums"].split(",")]
    if len(set(numbers)) != len(numbers):
        raise Refused("--merge lists a line more than once")
    return rel, numbers


class Tree:
    """The tracked paths of one repo at one sha, with their directories and base names."""

    def __init__(self, paths: list[str]) -> None:
        self.paths = set(paths)
        self.dirs = {"/".join(p.split("/")[:i]) for p in paths for i in range(1, p.count("/") + 1)}
        self.names = {p.rsplit("/", 1)[-1] for p in paths}

    def has(self, token: str) -> bool:
        """A tracked file, a tracked directory, or (no '/') a tracked base name."""
        if "/" not in token:
            return token in self.names or token in self.dirs
        return token in self.paths or token in self.dirs


def gone_reason(token: str, tree: Tree, deleted: dict[str, str]) -> str | None:
    """Why `token` counts as gone, or None. Gone needs both: nothing tracked answers to it, and
    git history shows a deletion of it (that exact path, a path under that directory, or for a
    bare name a file of that base name). Without the history proof (config keys, URLs, refs,
    package names, never-tracked runtime files) it is not listed."""
    bare = token.strip("/")
    if not bare or tree.has(bare):
        return None
    for path, sha in deleted.items():
        if "/" not in bare and path.rsplit("/", 1)[-1] == bare:
            return f"no tracked file named {bare}; {path} was deleted in {sha}"
        if path == bare:
            return f"not tracked; deleted in {sha}"
        if path.startswith(bare + "/"):
            return f"nothing tracked under {bare}/; {path} was deleted in {sha}"
    return None
