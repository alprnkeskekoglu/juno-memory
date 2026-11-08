from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from juno import config
from juno.core import fold, vaultfmt
from juno.core.inject import CONTEXT_LABEL, RULES_LABEL, neutralize, trim
from juno.core.trim import trim_pack
from juno.core.view import VaultView

MAX_THREAD_LINES = 5
SUBAGENT_HEADER = (
    "Juno memory applies in this subagent. Subagents cannot write the Juno vault; they can read "
    "vault files and run `juno search`, `juno recap`, `juno tasks`, `juno ideas`, `juno stale`, "
    "`juno rule list`, `juno backlog list` and `juno backlog show`."
)
Card = dict[str, Any]
Wanted = list[tuple[str, str]]


@dataclass
class NextPlan:
    stored: dict[str, str]
    show: bool
    seg: str = ""


@dataclass(frozen=True)
class Caps:
    pack_chars: int = 4500
    max_full_packs: int = 4
    pack_cards: int = 2
    pack_threads_chars: int = 500
    pack_knowledge_chars: int = 300
    overview_chars: int = 3000
    overview_cards: int = 2
    subagent_chars: int = 3000
    subagent_global_rules_chars: int = 2500
    active_ticket_days: int = 14
    purpose: bool = False
    linked: bool = False

    @classmethod
    def from_config(cls, cfg: config.Config) -> Caps:
        return cls(
            pack_chars=cfg.get_int("caps", "pack_chars"),
            max_full_packs=cfg.get_int("caps", "max_full_packs"),
            pack_cards=cfg.get_int("caps", "pack_cards"),
            pack_threads_chars=cfg.get_int("caps", "pack_threads_chars"),
            pack_knowledge_chars=cfg.get_int("caps", "pack_knowledge_chars"),
            overview_chars=cfg.get_int("caps", "overview_chars"),
            overview_cards=cfg.get_int("caps", "overview_cards"),
            subagent_chars=cfg.get_int("caps", "subagent_chars"),
            subagent_global_rules_chars=cfg.get_int("caps", "subagent_global_rules_chars"),
            active_ticket_days=cfg.get_int("thresholds", "active_ticket_days"),
            purpose=cfg.get_bool("packs", "purpose"),
            linked=cfg.get_bool("links", "linked_block"),
        )


def all_cards(view: VaultView) -> list[Card]:
    cards: list[Card] = []
    for name in view.names("handoffs"):
        rel = f"handoffs/{name}"
        text = view.read(rel)
        if view.hidden(rel, text):
            continue
        cards.extend(c for c in vaultfmt.parse_cards(text, rel) if c["valid"])
    return sorted(cards, key=lambda c: str(c["time"]), reverse=True)


def pack_sources(kind: str, key: str) -> dict[str, str]:
    if kind == "ticket":
        return {
            "rules": f"rules/{key}.md",
            "doc": f"tasks/{key}.md",
            "decisions": f"decisions/{key}.md",
        }
    name = fold.safe_name(key)
    return {
        "rules": f"rules/repos/{name}.md",
        "doc": f"projects/{name}.md",
        "decisions": f"decisions/{name}.md",
    }


def context_sections(kind: str, purpose: bool = False) -> tuple[list[str], list[str]]:
    if kind == "ticket":
        return ["Notes"], []
    return ["Gotchas", "How to run"], ["Purpose"] if purpose else []


def own_files(kind: str, key: str) -> set[str]:
    return set(pack_sources(kind, key).values())


def visible_rules(view: VaultView, rel: str) -> list[str]:
    text = view.read(rel)
    return [] if not text or view.hidden(rel, text) else vaultfmt.rule_texts(text)


def threads_block(view: VaultView, key: str, caps: Caps) -> str:
    text = view.read("threads.md")
    if not text or view.hidden("threads.md", text):
        return ""
    return vaultfmt.thread_lines(text, key, caps.pack_threads_chars, MAX_THREAD_LINES)


def knowledge_titles(
    view: VaultView, kind: str, key: str, caps: Caps, folder: str = "knowledge"
) -> str:
    picked: list[str] = []
    used = 0
    for name in view.names(folder):
        rel = f"{folder}/{name}"
        text = view.read(rel)
        if view.hidden(rel, text):
            continue
        fields, _ = vaultfmt.parse_frontmatter(text)
        values = vaultfmt.field_items(fields, "tickets" if kind == "ticket" else "repos")
        if key.casefold() not in (v.casefold() for v in values):
            continue
        title = str(fields.get("title") or "").strip()
        line = f"- {title} · {rel}{view.stale_mark(rel)}"
        if title and used + len(line) + 1 <= caps.pack_knowledge_chars:
            picked.append(line)
            used += len(line) + 1
    return "\n".join(picked)


def optional_blocks(
    view: VaultView, kind: str, key: str, caps: Caps, nexts: list[tuple[str, str]] | None = None
) -> list[tuple[str, str]]:
    return [
        *(nexts or []),
        ("Threads · threads.md", threads_block(view, key, caps)),
        ("Knowledge · knowledge/", knowledge_titles(view, kind, key, caps)),
        ("Procedure · procedures/", knowledge_titles(view, kind, key, caps, "procedures")),
        (
            "Linked · wikilinks",
            view.linked_titles(kind, key, own_files(kind, key)) if caps.linked else "",
        ),
    ]


def missing_task_hint(key: str) -> str:
    return (
        "missing. Fill it once with the title and status from the session's Jira tool: "
        f'`juno task set {key} --title "<title>" --status "<status>"`.'
    )


def render_pack(
    view: VaultView,
    caps: Caps,
    kind: str,
    key: str,
    cards: list[Card],
    shown: set[str] | None = None,
    plan: NextPlan | None = None,
) -> str:
    shown = set() if shown is None else shown
    src = pack_sources(kind, key)
    rules = visible_rules(view, src["rules"])
    doc = view.read(src["doc"])
    if view.hidden(src["doc"], doc):
        doc = ""
    decisions_text = view.read(src["decisions"])
    current = (
        ""
        if view.hidden(src["decisions"], decisions_text)
        else vaultfmt.decision_current(decisions_text)
    )
    head, tail = context_sections(kind, caps.purpose)
    doc_title = src["doc"] + view.stale_mark(src["doc"])
    blocks: list[tuple[str, str]] = [(f"Decisions · {src['decisions']}", current)]
    blocks += [(f"{n} · {doc_title}", vaultfmt.section(doc, n)) for n in head]
    mentioned = [
        c for c in cards if c["body"] not in shown and vaultfmt.card_mentions(c, kind, key)
    ][: caps.pack_cards]
    shown.update(c["body"] for c in mentioned)
    nexts: list[tuple[str, str]] = []
    if plan:
        seg = next((str(c["seg"]) for c in mentioned if str(c["seg"]) in plan.stored), "")
        plan.seg = seg
        if seg and plan.show:
            nexts = [(f"Next · {seg}", plan.stored[seg])]
    blocks += [("Card · " + c["path"], c["body"]) for c in mentioned]
    blocks += [(f"{n} · {doc_title}", vaultfmt.section(doc, n)) for n in tail]
    if kind == "ticket" and not doc:
        blocks.append(("Task file · " + src["doc"], missing_task_hint(key)))
    blocks = [(title, neutralize(body)) for title, body in blocks if body]
    extras = [
        (title, neutralize(body))
        for title, body in optional_blocks(view, kind, key, caps, nexts)
        if body
    ]
    if not rules and not blocks and not extras:
        return ""
    lines = [f"=== Juno pack: {kind} {key} ==="]
    if rules:
        lines += [RULES_LABEL + ":"] + ["- " + neutralize(r) for r in rules]
    if blocks:
        lines.append(CONTEXT_LABEL + ":")
    text = "\n".join(lines + [f"[{title}]\n{body}" for title, body in blocks])
    if blocks and len(text) > caps.pack_chars:
        return trim_pack(lines, blocks + extras, caps.pack_chars, text)
    text = trim(text, caps.pack_chars, src["doc"])
    has_label = bool(blocks)
    for title, body in extras:
        chunk = f"[{title}]\n{body}"
        if not has_label:
            chunk = CONTEXT_LABEL + ":\n" + chunk
        if len(text) + 1 + len(chunk) <= caps.pack_chars:
            text += "\n" + chunk
            has_label = True
    return text


def pack_pointer(view: VaultView, kind: str, key: str) -> str:
    src = pack_sources(kind, key)
    files = [
        p
        for p in (src["rules"], src["doc"], src["decisions"])
        if view.exists(p) and not view.hidden(p)
    ]
    where = ", ".join(files) if files else "handoff cards in handoffs/"
    return f"Juno pointer ({CONTEXT_LABEL}): {kind} {key} has memory in {where}."


def wanted_for(repos: dict[str, str | None]) -> Wanted:
    wanted: Wanted = []
    for repo, ticket in repos.items():
        if ticket:
            wanted.append(("ticket", ticket))
        wanted.append(("repo", repo))
    return wanted


def scoped_rule_lines(view: VaultView, wanted: Wanted) -> list[str]:
    lines: list[str] = []
    for kind, key in wanted:
        rel = pack_sources(kind, key)["rules"]
        texts = visible_rules(view, rel)
        if texts:
            lines += [f"[{kind} {key} · {rel}]"] + ["- " + neutralize(t) for t in texts]
    return lines


def subagent_rules(view: VaultView, caps: Caps, scoped: list[str], budget: int) -> str:
    room = min(caps.subagent_global_rules_chars, budget - len("\n".join(scoped)) - 80)
    texts = visible_rules(view, "rules/global.md")
    lines: list[str] = []
    if texts and room > 0:
        body = "\n".join("- " + neutralize(t) for t in texts)
        lines = ["[Global rules · rules/global.md]", trim(body, room, "rules/global.md")]
    lines += scoped
    return "\n".join([RULES_LABEL + ":", *lines]) if lines else ""


def subagent_block(
    view: VaultView,
    caps: Caps,
    wanted: Wanted,
    topic_lines: Callable[[int], list[str]],
) -> str:
    cards = all_cards(view) if wanted else []
    pointers = [
        pack_pointer(view, kind, key)
        for kind, key in wanted
        if render_pack(view, caps, kind, key, cards)
    ]
    context = "\n".join([CONTEXT_LABEL + ":", *pointers]) if pointers else ""
    budget = max(0, caps.subagent_chars - len(SUBAGENT_HEADER) - len(context) - 4)
    scoped = scoped_rule_lines(view, wanted)
    scoped += topic_lines(budget - len("\n".join(scoped)) - 80)
    rules = subagent_rules(view, caps, scoped, budget)
    if not rules and not context:
        return ""
    parts = [SUBAGENT_HEADER, trim(rules, budget, "the vault") if rules else "", context]
    return trim("\n\n".join(p for p in parts if p), caps.subagent_chars, "the vault")


def ideas_summary(view: VaultView) -> list[str]:
    counts: dict[str, int] = {}
    for name in view.names("ideas"):
        rel = f"ideas/{name}"
        text = view.read(rel)
        if view.hidden(rel, text):
            continue
        fields, _ = vaultfmt.parse_frontmatter(text)
        status = vaultfmt.field_str(fields, "status")
        counts[status] = counts.get(status, 0) + 1
    labels = (("in-progress", "in progress"), ("planned", "planned"), ("seed", "seeds"))
    shown = [f"{counts[s]} {label}" for s, label in labels if counts.get(s)]
    return ["ideas: " + ", ".join(shown)] if shown else []


def active_tickets(view: VaultView, caps: Caps, now: float) -> list[str]:
    cutoff = now - caps.active_ticket_days * 86400
    found: list[tuple[float, str]] = []
    for name in view.names("tasks"):
        rel = f"tasks/{name}"
        stamp = view.mtime(rel)
        if stamp < cutoff:
            continue
        text = view.read(rel)
        fields, _ = vaultfmt.parse_frontmatter(text)
        if vaultfmt.field_str(fields, "status") in vaultfmt.FINISHED_STATUSES or view.hidden(
            rel, text
        ):
            continue
        title = fields.get("title") or "-"
        repos = fields.get("repos") or "-"
        repos_text = ", ".join(repos) if isinstance(repos, list) else repos
        found.append((stamp, neutralize(f"- {name[:-3]} · {title} · repos: {repos_text}")))
    return [line for _, line in sorted(found, reverse=True)]


def overview(
    view: VaultView, caps: Caps, cards: list[Card], now: float, tickets_enabled: bool
) -> str:
    lines = [f"=== Juno overview ({CONTEXT_LABEL}) ==="]
    if tickets_enabled:
        lines.append(f"Active tickets (last {caps.active_ticket_days} days):")
        lines += active_tickets(view, caps, now) or ["- none"]
    newest = cards[: caps.overview_cards]
    if newest:
        lines.append("Newest cards:")
        lines += [neutralize(str(c["body"])) for c in newest]
    lines.append("Learned digest: _generated/learned.md")
    lines += ideas_summary(view)
    return trim("\n".join(lines), caps.overview_chars, "the vault")
