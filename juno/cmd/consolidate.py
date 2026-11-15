import argparse
import os
from collections.abc import Mapping

from juno import config
from juno.adapters import gate, repos, stalefix
from juno.cmd import write
from juno.core import consolidate, stale, vaultfmt
from juno.core.errors import Refused


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", help="vault-relative file, e.g. projects/<repo>.md")
    parser.add_argument("--section", help="the ## section to scan, e.g. Gotchas (default: all)")


def pin_for(cfg: config.Config, name: str) -> stalefix.Pin | None:
    """The default-branch pin of a repo as last fetched; no fetch, nothing written."""
    directory = repos.repo_dir(cfg, name)
    if directory is None:
        return None
    order = cfg.get_list("stale", "branch_order")
    found = (
        repos.default_ref(directory, order)
        if repos.has_origin(directory)
        else repos.local_default_ref(directory, order)
    )
    return stalefix.Pin(name, directory, found[0], found[1], "skipped") if found else None


def gone_paths(cfg: config.Config, rel: str, text: str, line: str) -> list[str]:
    kind = "knowledge" if vaultfmt.kind_of(rel) == "knowledge" else "profile"
    fields, _ = vaultfmt.parse_frontmatter(text)
    known = stale.file_repos(kind, fields, os.path.basename(rel))
    pins: dict[str, stalefix.Pin | None] = {}
    gone: list[str] = []
    for token in stale.path_tokens(line):
        tried = unchecked = found = False
        for name, path in stale.candidates(
            token, known, lambda n: repos.repo_dir(cfg, n) is not None
        ):
            if name not in pins:
                pins[name] = pin_for(cfg, name)
            pin = pins[name]
            if pin is None:
                unchecked = True
            elif stalefix.exists_at(pin, path):
                found = True
                break
            else:
                tried = True
        if tried and not found and not unchecked:
            gone.append(token)
    return gone


def do(cfg: config.Config, args: argparse.Namespace) -> int:
    rel = args.file.strip().replace("\\", "/").lstrip("/")
    if not rel.endswith(".md") or ".." in rel.split("/"):
        raise Refused("give a vault-relative .md file, e.g. projects/<repo>.md")
    text = gate.read(cfg, rel)
    if not text:
        raise Refused(f"{rel} is empty or does not exist")
    lines = text.splitlines()
    start, end = consolidate.section_bounds(lines, args.section)
    items = [(n, lines[n - 1]) for n in range(start + 1, end + 1) if lines[n - 1].startswith("- ")]
    where = f"{rel} ## {args.section}" if args.section else rel
    out = [f"Juno: {where}: {len(items)} lines (read-only; nothing was written)"]
    groups = consolidate.near_groups(items, write.preset(cfg))
    out.append(
        "Near-duplicate groups (same check as pitfall duplicates):"
        if groups
        else "no near-duplicate lines"
    )
    for number, group in enumerate(groups, 1):
        out.append(f"  group {number}: lines {', '.join(str(n) for n in group)}")
        out += [f"    {n}: {lines[n - 1]}" for n in group]
        out.append(
            f"    merge: juno fix --merge {rel}:{','.join(str(n) for n in group)} "
            + "--was <fragment> " * len(group)
            + '--evidence E "<one new line>"'
        )
    found = [(n, line, gone_paths(cfg, rel, text, line)) for n, line in items]
    gone = [g for g in found if g[2]]
    if gone:
        out.append("Lines citing a file that is gone from the repo's default branch (as fetched):")
        for n, line, paths in gone:
            out += [f"  {n}: {line}", f"      gone: {', '.join(paths)}"]
    print("\n".join(out))
    return 0


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    return write.execute(args, env, lambda cfg: do(cfg, args))
