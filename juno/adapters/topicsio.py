import contextlib
import json
import os
from typing import Any

from juno import config
from juno.adapters import fs, store
from juno.adapters.vaultview import DiskVault
from juno.core import topics
from juno.core.ledger import safe_id

INDEX_FILE = "triggers.json"
INDEX_VERSION = 3


def preset_of(cfg: config.Config) -> str:
    return cfg.get_str("language", "preset")


def signature(cfg: config.Config) -> list[list[Any]]:
    root = cfg.vault
    sig: list[list[Any]] = []
    stamp = store.file_sig(os.path.join(root, topics.PITFALLS_FILE))
    if stamp:
        sig.append([topics.PITFALLS_FILE, *stamp])
    for sub in (f"rules/{topics.TOPIC_DIR}", "knowledge", topics.PROCEDURE_DIR):
        directory = os.path.join(root, *sub.split("/"))
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            if sub == "knowledge":
                wanted = bool(topics.KNOWLEDGE_RE.match(name))
            elif sub == topics.PROCEDURE_DIR:
                wanted = name.endswith(".md") and bool(topics.SLUG_RE.match(name[:-3]))
            else:
                wanted = topics.topic_slug(f"{topics.TOPIC_DIR}/{name}") is not None
            if wanted:
                stamp = store.file_sig(os.path.join(directory, name))
                if stamp:
                    sig.append([f"{sub}/{name}", *stamp])
    return sig


def index(cfg: config.Config) -> list[topics.Entry]:
    sig = signature(cfg)
    if not sig:
        return []
    preset = preset_of(cfg)
    path = os.path.join(cfg.state, INDEX_FILE)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if (
            isinstance(data, dict)
            and data.get("v") == INDEX_VERSION
            and data.get("preset") == preset
            and data.get("sig") == sig
            and isinstance(data.get("entries"), list)
        ):
            return list(data["entries"])
    except OSError, ValueError:
        pass
    files = [(row[0], store.read_text(os.path.join(cfg.vault, *row[0].split("/")))) for row in sig]
    entries = topics.build_entries(files, preset)
    try:
        doc = {"v": INDEX_VERSION, "preset": preset, "sig": sig, "entries": entries}
        store.state_dir(cfg)
        fs.atomic_write(path, (json.dumps(doc, ensure_ascii=False) + "\n").encode(), 0o600)
    except OSError as exc:
        store.log_error(cfg, "topics-index", exc)
    return entries


def marker(cfg: config.Config, sid: object, agent: object, name: str) -> str | None:
    if not safe_id(sid):
        return None
    return os.path.join(store.state_dir(cfg, "topics"), f"{sid}.{topics.who(agent)}.{name}")


def claim(path: str) -> bool:
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        return False
    return True


def reset(cfg: config.Config, sid: object, agent: object = None) -> None:
    if not safe_id(sid):
        return
    prefix = f"{sid}.{topics.who(agent)}."
    directory = store.state_dir(cfg, "topics")
    for name in os.listdir(directory):
        if name.startswith(prefix):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(os.path.join(directory, name))


def active_topics(cfg: config.Config, sid: object) -> list[str]:
    if not safe_id(sid):
        return []
    prefix = f"{sid}.main.topic-"
    try:
        names = sorted(os.listdir(os.path.join(cfg.state, "topics")))
    except OSError:
        return []
    return [n[len(prefix) - len("topic-") :] for n in names if n.startswith(prefix)]


def claimer(cfg: config.Config, sid: object, agent: object) -> Any:
    def attempt(name: str) -> bool:
        path = marker(cfg, sid, agent, name)
        return path is not None and claim(path)

    return attempt


def for_text(
    cfg: config.Config,
    sid: object,
    agent: object,
    text: str,
    budget: int,
    view: DiskVault | None = None,
) -> str:
    preset = preset_of(cfg)
    hits = topics.matches(index(cfg), text, preset)
    if not hits:
        return ""
    view = view or DiskVault(cfg)
    again = frozenset(h[0] for h in hits if topics.repeats(h, text, preset))
    return topics.render(view, topics.ordered(hits), claimer(cfg, sid, agent), budget, 0, again)


def for_tool(cfg: config.Config, payload: dict[str, Any], budget: int) -> str:
    tool_in = payload.get("tool_input")
    text = topics.tool_text(payload.get("tool_name"), tool_in if isinstance(tool_in, dict) else {})
    return for_text(cfg, payload.get("session_id"), payload.get("agent_id"), text, budget)


def for_subagent(cfg: config.Config, sid: object, agent: object, budget: int) -> list[str]:
    if not agent or budget <= 0:
        return []
    view = DiskVault(cfg)
    blocks: list[str] = []
    used = 0
    for name in active_topics(cfg, sid):
        path = marker(cfg, sid, agent, name)
        slug = name[len("topic-") :]
        relpath = topics.topic_relpath(slug)
        block = (
            topics.topic_block(view, name, relpath)
            if path and topics.topic_slug(f"{topics.TOPIC_DIR}/{slug}.md")
            else ""
        )
        if not block or used + len(block) + 1 > budget or path is None or not claim(path):
            continue
        blocks.append(block)
        used += len(block) + 1
    return blocks


def general_block(cfg: config.Config) -> str:
    return topics.general_block(DiskVault(cfg), preset_of(cfg))
