import contextlib
import json
import os
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from juno import config
from juno.adapters import fs, store

VERSION = 1


class QueueError(ValueError):
    pass


@dataclass(frozen=True)
class Queue:
    name: str
    file: str
    letter: str
    max_bytes: int
    noun: str

    @property
    def hint(self) -> str:
        return "juno backlog list" if self.noun == "backlog" else f"juno {self.noun} pending"

    def valid_id(self, value: object) -> bool:
        return re.fullmatch(self.letter + "[0-9a-f]{6}", str(value)) is not None


RULES = Queue("candidates", "candidates.json", "r", 512 * 1024, "rule")
THREADS = Queue("thread-candidates", "thread-candidates.json", "t", 256 * 1024, "thread")
BACKLOG = Queue("backlog-suggestions", "backlog-suggestions.json", "b", 256 * 1024, "backlog")

Item = dict[str, Any]


def path(cfg: config.Config, queue: Queue) -> str:
    return os.path.join(cfg.state, "cards", queue.file)


def read(cfg: config.Config, queue: Queue) -> list[Item]:
    target = path(cfg, queue)
    try:
        if os.path.getsize(target) > queue.max_bytes:
            return []
    except OSError:
        return []
    data = store.read_json_dict(target)
    items = data.get("items")
    if data.get("v") != VERSION or not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict) and queue.valid_id(i.get("id"))]


def write(cfg: config.Config, queue: Queue, items: list[Item]) -> None:
    store.state_dir(cfg, "cards")
    doc = {"v": VERSION, "items": items}
    text = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    fs.atomic_write(path(cfg, queue), text.encode("utf-8"), 0o600)


@contextlib.contextmanager
def locked(cfg: config.Config, queue: Queue, timeout: float = 2.0) -> Iterator[None]:
    store.state_dir(cfg, "cards")
    try:
        with fs.locked(os.path.join(cfg.state, "cards", queue.name + ".lock"), timeout):
            yield
    except TimeoutError:
        raise QueueError(f"the {queue.noun} queue is busy; retry in a moment") from None


def pending(cfg: config.Config, queue: Queue) -> list[Item]:
    return [i for i in read(cfg, queue) if i.get("status") == "pending"]


def clean_ids(queue: Queue, raw: list[str]) -> list[str]:
    ids: list[str] = []
    for value in raw:
        cid = value.strip().casefold()
        if not queue.valid_id(cid):
            raise QueueError(
                f"{value!r} is not a {queue.noun} id ({queue.letter} and 6 hex digits; "
                f"see `{queue.hint}`)"
            )
        if cid not in ids:
            ids.append(cid)
    return ids


def pending_by_id(items: list[Item], queue: Queue, ids: list[str]) -> list[Item]:
    found = {i["id"]: i for i in items if i.get("status") == "pending"}
    missing = [c for c in ids if c not in found]
    if missing:
        raise QueueError(
            f"no pending {queue.noun} candidate {', '.join(missing)} (see `{queue.hint}`)"
        )
    return [found[c] for c in ids]


def mark(items: list[Item], status: str, now: float | None = None) -> None:
    stamp = time.time() if now is None else now
    for item in items:
        item.update(status=status, decided_t=stamp)


def update[T](cfg: config.Config, queue: Queue, change: Callable[[list[Item]], T]) -> T:
    with locked(cfg, queue, 10.0):
        items = read(cfg, queue)
        result = change(items)
        write(cfg, queue, items)
        return result
