from typing import Any

from juno import config, hookcore
from juno.adapters import ledgerio, repos, shadowio, status, store, topicsio, voiceio
from juno.adapters.vaultview import DiskVault
from juno.core import fold, inject, pinned, prompts
from juno.core.ledger import Segment
from juno.core.redact import chunks, redact
from juno.handlers import delivery
from juno.handlers.common import capture_title, main_session, output

NOSAVE_SET_LINE = (
    "Juno: [nosave] is set for this segment: nothing is written to the vault and no handoff "
    "card is made."
)
NOSAVE_MENTION_LINE = (
    "Juno: [nosave] was mentioned but not set; it counts only as the first or last word of a "
    "message."
)
CUE_LINE = (
    "If this is a lasting correction or preference, add it with `juno rule add` "
    "using my exact words."
)


def store_prompt(
    cfg: config.Config,
    seg: Segment,
    prompt_id: object,
    typed: str,
    tickets: list[str],
    nosave: bool,
) -> None:
    pid = str(prompt_id or "-")[:64]
    matcher = repos.tickets_from(cfg)
    for i, piece in enumerate(chunks(typed)):
        rec: dict[str, Any] = {"ev": "prompt", "pid": pid, "part": i, "text": piece}
        if i == 0:
            rec.update(
                tickets=tickets[:20],
                cmd_tickets=matcher.command_tickets(typed)[:20],
                nosave=nosave,
            )
        ledgerio.record(cfg, seg, rec)


def nosave_notice(seg: Segment, own: str, nosave: bool) -> str:
    if seg.nosave:
        return ""
    if nosave:
        return NOSAVE_SET_LINE
    return NOSAVE_MENTION_LINE if prompts.nosave_mentioned(own) else ""


def handle(payload: dict[str, Any], cfg: config.Config) -> dict[str, Any] | None:
    if not main_session(payload):
        return None
    try:
        return handle_prompt(payload, cfg)
    except OSError as exc:
        if not hookcore.state_unwritable(cfg, exc):
            raise
        ledgerio.mark_unrecorded(cfg, payload.get("session_id"))
        store.log_error(cfg, "user_prompt_submit", exc)
        seg = ledgerio.fold(cfg, payload.get("session_id"))
        text = "\n\n".join(t for t in (pins_for(cfg, seg), hookcore.STATE_LINE) if t)
        return output(cfg, "UserPromptSubmit", text)


def pins_for(cfg: config.Config, seg: Segment) -> str:
    return pinned.pinned_block(
        DiskVault(cfg),
        list(seg.repos),
        list(seg.topics_fired),
        min(cfg.get_int("caps", "pinned_rules_chars"), cfg.get_int("caps", "hook_output_chars")),
    )


def handle_prompt(payload: dict[str, Any], cfg: config.Config) -> dict[str, Any] | None:
    sid = payload.get("session_id")
    prompt = payload.get("prompt")
    prompt = fold.nfc(prompt) if isinstance(prompt, str) else ""
    seg = ledgerio.fold(cfg, sid)
    capture_title(cfg, seg, payload)
    view = DiskVault(cfg)
    cap = cfg.get_int("caps", "hook_output_chars")
    pins = pins_for(cfg, seg)
    if prompts.is_synthetic(prompt):
        status.update(cfg, seg)
        return output(cfg, "UserPromptSubmit", pins) if pins else None
    own = prompts.user_text(prompt) if prompt.strip() else prompt
    typed = redact(prompts.strip_pastes(own))
    tickets = repos.tickets_from(cfg).find(typed)
    nosave = prompts.nosave_set(own)
    notice = nosave_notice(seg, own, nosave)
    store_prompt(cfg, seg, payload.get("prompt_id"), typed, tickets, nosave)
    preset = cfg.get_str("language", "preset")
    cue = CUE_LINE if fold.has_cue(typed, cfg.language_list("correction_cues"), preset) else ""
    voice = voiceio.on_prompt(cfg, seg, typed, nosave)
    budget = (
        cap
        - (len(pins) + 2 if pins else 0)
        - len(cue)
        - 2
        - (len(notice) + 2 if notice else 0)
        - (len(voice) + 2 if voice else 0)
    )
    before = set(seg.packs)
    delivered = delivery.deliver_packs(
        cfg, view, seg, [("ticket", k) for k in tickets], budget, typed
    )
    topic_text = topicsio.for_text(cfg, sid, None, typed, budget - len(delivered) - 2, view)
    status.update(cfg, seg)
    shadowio.observe(cfg, seg, view, typed, own, nosave, tickets, before, delivered)
    text = "\n\n".join(t for t in (pins, notice, cue, voice, delivered, topic_text) if t)
    return output(cfg, "UserPromptSubmit", inject.trim(text, cap, "the vault"))
