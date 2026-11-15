"""Pure helpers for cards of one segment that sit in several handoff files."""

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from juno.core import fold

Card = Mapping[str, Any]
Check = tuple[str, str, str]
MISFILED_SHOWN = 10
MISC = "handoffs/misc.md"


def stem(card: Card) -> str:
    return str(card["path"]).removeprefix("handoffs/").removesuffix(".md")


def names_file(card: Card) -> bool:
    """The card's file is named after one of its repos or its ticket key."""
    named = [fold.safe_name(str(r)) for r in card.get("repos") or []]
    key = str(card.get("key") or "-")
    if key != "-":
        named.append(fold.safe_name(key))
    return stem(card) in named


def copies(cards: Sequence[Card]) -> dict[str, list[Card]]:
    """Valid cards by segment id, only segments filed in more than one handoff file."""
    by_seg: dict[str, list[Card]] = defaultdict(list)
    for card in cards:
        if card.get("valid"):
            by_seg[str(card["seg"])].append(card)
    return {seg: group for seg, group in by_seg.items() if len({str(c["path"]) for c in group}) > 1}


def repo_stems(cards: Sequence[Card], profiles: Collection[str] = ()) -> set[str]:
    """File stems that are a repo: named by some card's `Repos:` line or with a vault profile."""
    named = {fold.safe_name(str(r)) for c in cards if c.get("valid") for r in c.get("repos") or []}
    return named | {fold.safe_name(p) for p in profiles}


def misfiled(cards: Sequence[Card], profiles: Collection[str] = ()) -> list[Card]:
    """Cards in a repo's handoff file while their own `Repos:` line lacks that repo."""
    repos = repo_stems(cards, profiles)
    return [c for c in cards if c.get("valid") and stem(c) in repos and not names_file(c)]


def extras(group: Sequence[Card]) -> list[Card]:
    """Copies of one segment to drop: every copy outside the files the card names.

    A card filed once in each file it names keeps all of them. With no named file, one copy
    stays: `handoffs/misc.md` when the card is there, else the first file by name.
    """
    ordered = sorted(group, key=lambda c: str(c["path"]))
    named = [c for c in ordered if names_file(c)]
    if named:
        return [c for c in ordered if not names_file(c)]
    home = next((c for c in ordered if str(c["path"]) == MISC), ordered[0])
    return [c for c in ordered if str(c["path"]) != str(home["path"])]


def extra_copies(cards: Sequence[Card]) -> list[Card]:
    """Copies to drop, always keeping at least one per segment."""
    return [c for group in copies(cards).values() for c in extras(group)]


def checks(cards: Sequence[Card], profiles: Collection[str] = ()) -> list[Check]:
    out: list[Check] = []
    for seg, group in sorted(copies(cards).items()):
        if not extras(group):
            continue
        files = sorted({str(c["path"]) for c in group})
        said = f"segment {seg} has a card in {len(files)} files ({', '.join(files)}); "
        out.append(("WARN", "card copies", said + "`juno migrate --cards` drops the extra copies"))
    stray = misfiled(cards, profiles)
    for card in stray[:MISFILED_SHOWN]:
        named = ", ".join(card["repos"]) or "-"
        said = f"{card['path']}: card {card['seg']} names {named}, not this repo"
        said += "; `juno migrate --cards` moves it"
        out.append(("WARN", "misfiled cards", said))
    if len(stray) > MISFILED_SHOWN:
        out.append(("WARN", "misfiled cards", f"{len(stray) - MISFILED_SHOWN} more"))
    return out
