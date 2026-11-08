from typing import Any

from juno.adapters import gate, store, vaultview
from juno.adapters.learnkinds.base import Ctx, list_md, status_of, today
from juno.core import topics, vaultfmt, writegate
from juno.core.errors import Refused
from juno.core.textchecks import SECOND_PERSON_RE, anchored, as_list, text_field

KEY = "procedures"
SHAPE = list
LOCKED = True
STATE_RANK = 5
MIN_STEPS, MAX_STEPS = 2, 12
STEP_CHARS = (5, 200)
TITLE_CHARS = (5, 80)
MAX_TRIGGERS = 5


def instructions(ctx: Ctx) -> list[str]:
    return [
        '- "procedures": at most 1 {"slug", "title", "steps", "triggers", "repos"}: a repeatable '
        "step sequence that worked in this segment and will be needed again (a release, a "
        'deploy check, a migration), not a one-off. "slug": lowercase words joined by `-`. '
        '"title": 5-80 chars. "steps": 2-12 strings of 5-200 chars, each copied or closely '
        "taken from a command or boundary in the digest. Never repo-specific run commands "
        '(those belong in the project profile). "triggers": 0-5 whole words or commands that '
        'mark when it applies. "repos": segment repos it is specific to, or []. No `·`, no '
        "secrets, no second person. Skip what the existing procedure files already cover."
    ]


def state(ctx: Ctx) -> str:
    names = [n[:-3] for n in list_md(ctx.cfg, "procedures")]
    return "Procedure files: " + (", ".join(names) or "-")


def check(ctx: Ctx, item: object) -> tuple[str | None, dict[str, Any]]:
    if not isinstance(item, dict):
        return "invalid", {}
    slug, title = text_field(item.get("slug")), text_field(item.get("title"))
    raw = item.get("steps")
    if not (isinstance(raw, list) and MIN_STEPS <= len(raw) <= MAX_STEPS):
        return "invalid", {}
    steps = [text_field(s) for s in raw]
    if not topics.SLUG_RE.match(slug) or not TITLE_CHARS[0] <= len(title) <= TITLE_CHARS[1]:
        return "invalid", {}
    for text in (title, *steps):
        if "·" in text or "[[" in text or SECOND_PERSON_RE.search(text):
            return "invalid", {}
    if any(not STEP_CHARS[0] <= len(s) <= STEP_CHARS[1] for s in steps):
        return "invalid", {}
    if not all(anchored(s, ctx.digest or "") for s in steps):
        return "unanchored", {}
    given = item.get("triggers")
    if given is not None and not (
        isinstance(given, list) and all(isinstance(v, str) for v in given)
    ):
        return "trigger", {}
    preset = ctx.cfg.get_str("language", "preset")
    triggers = topics.split_triggers(",".join(str(v) for v in as_list(given)), preset)
    if len(triggers) > MAX_TRIGGERS or any(topics.trigger_problem(t) for t in triggers):
        return "trigger", {}
    known = {r.casefold(): r for r in ctx.seg.repos}
    named = [
        known[str(r).casefold()] for r in as_list(item.get("repos")) if str(r).casefold() in known
    ]
    return None, {
        "slug": slug,
        "title": title,
        "steps": steps,
        "triggers": triggers,
        "repos": list(dict.fromkeys(named)),
    }


def write(ctx: Ctx, found: dict[str, Any]) -> str | None:
    cfg = ctx.cfg
    rel = f"procedures/{found['slug']}.md"
    old = store.read_text(gate.vault_file(cfg, rel))
    if old and vaultfmt.hidden_reason(rel, old, vaultview.read_retired(cfg.state)):
        return "path"
    try:
        title = gate.scan_line(cfg, found["title"], "title")
        steps = [gate.scan_line(cfg, s, "step") for s in found["steps"]]
        text = topics.procedure_text(
            title, today(), ctx.seg.sid[:8], steps, found["triggers"], found["repos"]
        )
        writegate.check_length(text, "procedure")
    except Refused:
        return "private"
    gate.commit(cfg, ctx.seg, "procedure", rel, text, "engine: card run", engine=True)
    return None


def apply(ctx: Ctx, data: dict[str, Any], sections: dict[str, str]) -> None:
    items = as_list(data.get(KEY))
    if not items:
        return
    problem, found = check(ctx, items[0])
    problem = problem or write(ctx, found)
    ctx.result[KEY] = {"written": [] if problem else [found["slug"]], "dropped": problem}
    sections[KEY] = status_of(1, 0 if problem else 1, {problem: 1} if problem else {})
