import argparse
import os
from collections.abc import Mapping

from juno import config
from juno.adapters.vaultview import DiskVault
from juno.cmd import write
from juno.core.backlog import as_list
from juno.core.vaultfmt import parse_frontmatter

STATUS_ORDER = ("in-progress", "planned", "seed", "done", "dropped")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--status")
    parser.add_argument("--tag")
    parser.add_argument("--json", action="store_true")


def title_of(body: str) -> str:
    lines = iter(body.split("\n"))
    for line in lines:
        if line.strip().casefold() == "## idea":
            break
    for line in lines:
        if line.startswith("## "):
            break
        if line.strip():
            return line.strip()
    return ""


def row(view: DiskVault, name: str) -> dict[str, object] | None:
    rel = f"ideas/{name}"
    text = view.read(rel)
    fields, body = parse_frontmatter(text)
    if not fields or view.hidden(rel, text):
        return None
    return {
        "id": name[:-3],
        "status": " ".join(as_list(fields.get("status"))).casefold(),
        "updated": " ".join(as_list(fields.get("updated"))),
        "tags": as_list(fields.get("tags")),
        "title": title_of(body),
        "source": " ".join(as_list(fields.get("source"))),
    }


def rank(status: object) -> int:
    return STATUS_ORDER.index(str(status)) if status in STATUS_ORDER else len(STATUS_ORDER)


def render(rows: list[dict[str, object]]) -> str:
    lines: list[str] = []
    last: object = None
    for r in rows:
        if r["status"] != last:
            last = r["status"]
            lines.append(f"{last or 'unknown'}:")
        tags = ", ".join(str(t) for t in r["tags"]) if isinstance(r["tags"], list) else ""
        lines.append(
            f"  {r['id']} · {r['updated'] or '-'} · {tags or '-'} · {r['title'] or '-'} · "
            f"{r['source'] or '-'}"
        )
    return "\n".join(lines)


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    def body(cfg: config.Config) -> int:
        if not os.path.isdir(cfg.vault):
            raise write.Refused(f"vault not found at {cfg.vault}")
        view = DiskVault(cfg)
        found = [r for r in (row(view, n) for n in view.names("ideas")) if r]
        if args.status:
            found = [r for r in found if r["status"] == args.status.casefold()]
        if args.tag:
            tag = args.tag.casefold()
            found = [
                r
                for r in found
                if isinstance(r["tags"], list) and tag in (str(t).casefold() for t in r["tags"])
            ]
        found.sort(key=lambda r: str(r["updated"]), reverse=True)
        found.sort(key=lambda r: rank(r["status"]))
        write.emit(args, {"ideas": found}, render(found) or "no ideas")
        return 0

    return write.execute(args, env, body)
