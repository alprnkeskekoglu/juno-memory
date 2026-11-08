import contextlib
import os

from juno import config
from juno.adapters import fs, gate, linksio, store, usageio
from juno.core import cardfmt, links, routing, vaultfmt
from juno.core.errors import Refused
from juno.core.ledger import Segment

EXISTING_CHARS = 1500
VAULT_LOCK = "vault.lock"


def vault_lock(
    cfg: config.Config, timeout: float = 10.0
) -> contextlib.AbstractContextManager[None]:
    return fs.locked(os.path.join(cfg.state, VAULT_LOCK), timeout)


def safe_path(cfg: config.Config, *parts: str) -> str | None:
    return store.vault_file(cfg, *parts)


def handoff_path(cfg: config.Config, name: str) -> str:
    if not cardfmt.HANDOFF_NAME_RE.fullmatch(name or ""):
        raise cardfmt.Failed(f"bad handoff file name: {name!r}")
    path = safe_path(cfg, "handoffs", name)
    if path is None:
        raise cardfmt.Failed("handoff path escapes handoffs/: " + name)
    return path


def target_name(target: routing.Target) -> str:
    rel = str(target["path"])
    if not rel.startswith("handoffs/") or "/" in rel[len("handoffs/") :]:
        raise cardfmt.Failed("target outside handoffs/: " + rel)
    return rel[len("handoffs/") :]


def reserved_names(cfg: config.Config) -> set[str]:
    names: set[str] = set()
    for root in [*cfg.repo_roots(), os.path.join(cfg.vault, "projects")]:
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        names.update(n[:-3].casefold() if n.endswith(".md") else n.casefold() for n in entries)
    return names


def handoff_names(cfg: config.Config) -> list[str]:
    try:
        listed = os.listdir(os.path.join(cfg.vault, "handoffs"))
    except OSError:
        return []
    return sorted(n[:-3] for n in listed if n.endswith(".md") and not n.startswith("."))


def existing_card(cfg: config.Config, targets: list[routing.Target], seg8: str) -> str:
    for target in targets:
        path = os.path.join(cfg.vault, *str(target["path"]).split("/"))
        for card in vaultfmt.parse_cards(store.read_text(path), str(target["path"])):
            if card.get("seg") == seg8:
                return str(card["body"])[:EXISTING_CHARS]
    return ""


def put_card(cfg: config.Config, seg: Segment, rel: str, card: str, name: str) -> str:
    seg8 = seg.sid[:8]
    path = gate.vault_file(cfg, rel)
    new = cardfmt.place_card(store.read_text(path), seg8, card, name)
    gate.commit(cfg, seg, "card", rel, new, f"segment {seg8}", run_cap=False)
    return path


def log_card_links(cfg: config.Config, seg: Segment, card: str) -> None:
    targets = [target for _, target in links.wikilinks(card)]
    if not targets:
        return
    with contextlib.suppress(OSError):
        index = links.build_index(linksio.link_map(cfg))
        found = (links.resolve_link(target, index) for target in targets)
        usageio.append(cfg.state, seg.sid, [rel for rel in found if rel], "card")


def write_card(
    cfg: config.Config, seg: Segment, targets: list[routing.Target], card: str
) -> list[str]:
    for target in targets:
        handoff_path(cfg, target_name(target))
    paths: list[str] = []
    try:
        with vault_lock(cfg):
            for target in targets:
                name = target_name(target)
                paths.append(put_card(cfg, seg, str(target["path"]), card, name[:-3]))
    except TimeoutError:
        raise cardfmt.Failed("vault lock busy") from None
    except Refused as exc:
        raise cardfmt.Failed(str(exc)) from None
    log_card_links(cfg, seg, card)
    return paths
