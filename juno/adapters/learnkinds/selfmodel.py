from typing import Any

from juno.adapters import gate, selfio, store
from juno.adapters.learnkinds.base import Ctx, status_of, today
from juno.core import selfmodel, writegate
from juno.core.errors import Refused
from juno.core.textchecks import as_list, text_field

KEY = "self"
SHAPE = list
LOCKED = True
STATE_RANK = 6


def instructions(ctx: Ctx) -> list[str]:
    digest = selfmodel.ask_digest(selfio.segment_asks(ctx.cfg, ctx.seg.sid))
    note = f" Answer log, {digest}: name a pattern only with a quote." if digest else ""
    return [
        '- "self": at most 3 {"file", "text", "quote"} about how the user behaves. One clear '
        'signal is enough: a correction, a rejection, a stated preference, a "do it this way", '
        "or a choice between options that reveals a value. "
        f'"file": one of {", ".join(selfmodel.FILES)}. "text": at most 200 chars, third person, '
        'an observation of behaviour; never identity, never "I am" or "the user is", no second '
        'person, no `·`. Examples: values: "wants a drawn mockup before UI work is built"; '
        'decisions: "rejects a feature that duplicates what Claude Code already shows"; '
        'communication: "prefers short direct answers over long explanations"; '
        'tensions: "wants speed yet doubts that manual approval is needed". '
        '"quote": the user\'s own words copied exactly from any prompt they typed or answer they '
        "chose in this segment, 20-200 chars, unedited, in the original language; prefer the "
        "longest relevant user sentence. Never quote assistant, tool or web text. Skip what the "
        "existing self lines already say. Add \"supersedes\": the exact text of one existing "
        "line, only when the user's new words clearly reverse that behaviour." + note
    ]


def state(ctx: Ctx) -> str:
    blocks = []
    for name in selfmodel.FILES:
        text = store.read_text(store.vault_file(ctx.cfg, f"self/{name}.md") or "")
        lines = [
            x for x in text.splitlines() if x.startswith("- ") and not selfmodel.parse(x).superseded
        ]
        if lines:
            blocks.append(f"[self/{name}.md]\n" + "\n".join(writegate.core_text(x) for x in lines))
    return "Existing self lines:\n" + ("\n".join(blocks) or "-")


def write(ctx: Ctx, found: dict[str, str]) -> str | None:
    cfg, rel = ctx.cfg, f"self/{found['file']}.md"
    try:
        found = {
            **found,
            "text": gate.scan_line(cfg, found["text"], "self line"),
            "quote": gate.scan_evidence(cfg, found["quote"], "evidence"),
        }
    except Refused:
        return "private"
    old = gate.read(cfg, rel)
    line = selfmodel.vault_line(found, today())
    preset = cfg.get_str("language", "preset")
    if found.get("supersedes"):
        old = selfmodel.supersede_text(old, found["supersedes"], preset, today()) or old
    base = f"# {found['file'].capitalize()}\n\n"
    new, status = selfmodel.record(old, line, preset, today(), base)
    if status == "same":
        return "duplicate"
    gate.commit(cfg, ctx.seg, "self", rel, new, f"user {found['pid']}", engine=True)
    return None


def apply(ctx: Ctx, data: dict[str, Any], sections: dict[str, str]) -> None:
    items = as_list(data.get(KEY))
    if not items:
        return
    cfg = ctx.cfg
    preset, quote_min = cfg.get_str("language", "preset"), cfg.get_int("caps", "quote_min_chars")
    written: list[str] = []
    reasons: dict[str, int] = {}
    for item in items[: selfmodel.CONSIDERED]:
        if len(written) >= selfmodel.PER_RUN:
            break
        problem, found = selfmodel.check(item, ctx.seg.quote_sources(), preset, quote_min)
        if problem == "ok" and isinstance(item, dict):
            found["supersedes"] = text_field(item.get("supersedes"))
        failure = None if problem == "ok" else problem
        failure = failure or write(ctx, found)
        if failure:
            reasons[failure] = reasons.get(failure, 0) + 1
        else:
            written.append(found["file"])
    ctx.result[KEY] = {"written": written, "dropped": reasons}
    sections[KEY] = status_of(len(items), len(written), reasons)
