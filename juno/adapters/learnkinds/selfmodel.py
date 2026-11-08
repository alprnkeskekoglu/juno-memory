from typing import Any

from juno.adapters import gate, selfio, store
from juno.adapters.learnkinds.base import Ctx, status_of, today
from juno.core import selfmodel, writegate
from juno.core.errors import Refused
from juno.core.textchecks import as_list

KEY = "self"
SHAPE = list
LOCKED = True
STATE_RANK = 6


def instructions(ctx: Ctx) -> list[str]:
    digest = selfmodel.ask_digest(selfio.segment_asks(ctx.cfg, ctx.seg.sid))
    note = f" Answer log, {digest}: name a pattern only with a quote." if digest else ""
    return [
        '- "self": at most 2 {"file", "text", "quote"} about how the user behaves, one only when '
        "this segment shows a pattern. "
        f'"file": one of {", ".join(selfmodel.FILES)}. "text": at most 200 chars, third person, '
        'an observation of behaviour ("prefers X over Y when Z"); never identity, never "I am" '
        'or "the user is", no second person, no `·`. "quote": the user\'s own words copied '
        "exactly from one typed prompt or chosen answer, 20-200 chars, unedited. Skip what the "
        "existing self lines already say." + note
    ]


def state(ctx: Ctx) -> str:
    blocks = []
    for name in selfmodel.FILES:
        text = store.read_text(store.vault_file(ctx.cfg, f"self/{name}.md") or "")
        lines = [line for line in text.splitlines() if line.startswith("- ")]
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
    items = [writegate.core_text(x) for x in old.splitlines() if x.startswith("- ")]
    if writegate.duplicate(items, writegate.core_text(line), preset):
        return "duplicate"
    new, _ = writegate.add_item(old, line, preset, base=f"# {found['file'].capitalize()}\n\n")
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
        failure = None if problem == "ok" else problem
        failure = failure or write(ctx, found)
        if failure:
            reasons[failure] = reasons.get(failure, 0) + 1
        else:
            written.append(found["file"])
    ctx.result[KEY] = {"written": written, "dropped": reasons}
    sections[KEY] = status_of(len(items), len(written), reasons)
