import os
import time

from juno import config
from juno.adapters import cardwrite, fs, gate, store
from juno.core import taskfile
from juno.core.errors import Refused
from juno.core.ledger import Segment
from juno.core.taskrows import Row, apply


def rel(key: str) -> str:
    return f"tasks/{key}.md"


def path(cfg: config.Config, key: str) -> str | None:
    return cardwrite.safe_path(cfg, "tasks", f"{key}.md")


def read(cfg: config.Config, key: str) -> str | None:
    target = path(cfg, key)
    raw = None if target is None else fs.read_bytes(target)
    return None if raw is None else raw.decode("utf-8", "replace")


def write_fields(
    cfg: config.Config, seg: Segment, key: str, updates: taskfile.Updates
) -> gate.Result:
    current = read(cfg, key)
    new = taskfile.merge(current or "", key, updates, time.strftime("%Y-%m-%d"))
    return gate.commit(cfg, seg, "task", rel(key), new, f"ticket {key}")


def put_rows(
    cfg: config.Config,
    seg: Segment,
    key: str,
    rows: list[Row],
    evidence: str,
    day: str,
    engine: bool = False,
) -> tuple[gate.Result, int, int]:
    """Write digest rows into tasks/<KEY>.md, creating it when missing: the one code path of
    `juno task log` and the session-end `ticket` kind. The caller holds the vault lock."""
    current = read(cfg, key)
    if current is None:
        if path(cfg, key) is None:
            raise Refused(f"{rel(key)} is outside the vault (symlink)")
        url = taskfile.default_url(cfg.get_str("jira", "base_url"), key)
        current = taskfile.stub(key, day, url)
    new, added, replaced = apply(current, key, rows, day)
    res = gate.commit(cfg, seg, "task-log", rel(key), new, evidence, engine=engine)
    return res, added, replaced


def rows(cfg: config.Config) -> list[dict[str, object]]:
    folder = os.path.join(cfg.vault, "tasks")
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".md") and not n.startswith("."))
    except OSError:
        return []
    return [taskfile.row(n[:-3], store.read_text(os.path.join(folder, n))) for n in names]
