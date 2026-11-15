import os
import time

from juno.adapters import fs
from juno.core import migrate, taskfile, writegate


def stale_exception(vault: str) -> None:
    path = os.path.join(vault, "core.md")
    raw = fs.read_bytes(path)
    if raw is None:
        return
    text = raw.decode("utf-8")
    updated = migrate.add_stale_exception(text)
    if updated != text:
        fs.atomic_write(path, updated.encode("utf-8"))


def _replace_in(vault: str, rel: str, old: str, new: str) -> None:
    path = os.path.join(vault, rel)
    raw = fs.read_bytes(path)
    if raw is None:
        return
    text = raw.decode("utf-8")
    updated = migrate.replace_exact(text, old, new)
    if updated != text:
        fs.atomic_write(path, updated.encode("utf-8"))


def quote_and_cap_text(vault: str) -> None:
    _replace_in(
        vault,
        os.path.join("protocol", "rules.md"),
        migrate.RULES_QUOTE_OLD,
        migrate.RULES_QUOTE_NEW,
    )
    _replace_in(vault, "core.md", migrate.CAPS_OLD, migrate.CAPS_NEW)


def card_link_reasons(vault: str) -> None:
    _replace_in(vault, "core.md", migrate.LINKS_FORMAT_OLD, migrate.LINKS_FORMAT_NEW)
    _replace_in(vault, "core.md", migrate.LINKS_CMD_OLD, migrate.LINKS_CMD_NEW)


def ticket_decisions(vault: str) -> None:
    day = time.strftime("%Y-%m-%d")
    folder = os.path.join(vault, "decisions")
    names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
    for name in names:
        stem = name[:-3]
        topic = taskfile.canonical_topic(stem)
        if not name.endswith(".md") or topic == stem:
            continue
        old = os.path.join(folder, name)
        text = (fs.read_bytes(old) or b"").decode("utf-8")
        target = os.path.join(folder, topic + ".md")
        # On a case-insensitive filesystem the target is the same file: rename in place.
        same = os.path.exists(target) and os.path.samefile(old, target)
        have = None if same else fs.read_bytes(target)
        if have is None:
            new = writegate.set_field(text, "topic", topic)
            fs.atomic_write(old, new.encode("utf-8"))
            os.rename(old, target)
        else:
            new = migrate.merge_decision_files(have.decode("utf-8"), text)
            fs.atomic_write(target, new.encode("utf-8"))
            os.remove(old)
    tasks = os.path.join(vault, "tasks")
    for name in sorted(os.listdir(tasks)) if os.path.isdir(tasks) else []:
        path = os.path.join(tasks, name)
        stem = name[:-3]
        if not name.endswith(".md") or not taskfile.TICKET_TOPIC_RE.match(stem):
            continue
        text = (fs.read_bytes(path) or b"").decode("utf-8")
        lines, rest = migrate.take_task_decisions(text)
        if rest == text:
            continue
        if lines:
            target = os.path.join(folder, taskfile.canonical_topic(stem) + ".md")
            have = fs.read_bytes(target)
            base = have.decode("utf-8") if have is not None else None
            base = base or migrate.new_decision_file(taskfile.canonical_topic(stem), day)
            merged = migrate.add_decisions(base, lines, "Current")
            if merged != base:
                merged = writegate.set_field(merged, "updated", day)
            fs.ensure_private_dir(folder)
            fs.atomic_write(target, merged.encode("utf-8"))
        fs.atomic_write(path, rest.encode("utf-8"))


STEPS: dict[int, migrate.Step] = {
    1: stale_exception,
    2: quote_and_cap_text,
    3: ticket_decisions,
    4: card_link_reasons,
}
