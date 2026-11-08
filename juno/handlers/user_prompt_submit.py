from typing import Any

from juno import config
from juno.adapters import ledgerio, repos, shadowio, status, topicsio
from juno.adapters.vaultview import DiskVault
from juno.core import fold, inject, prompts
from juno.core.ledger import Segment
from juno.core.redact import chunks, redact
from juno.handlers import delivery
from juno.handlers.common import capture_title, main_session, output

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


def handle(payload: dict[str, Any], cfg: config.Config) -> dict[str, Any] | None:
    if not main_session(payload):
        return None
    sid = payload.get("session_id")
    prompt = payload.get("prompt")
    prompt = fold.nfc(prompt) if isinstance(prompt, str) else ""
    seg = ledgerio.fold(cfg, sid)
    capture_title(cfg, seg, payload)
    if prompts.is_synthetic(prompt):
        status.update(cfg, seg)
        return None
    own = prompts.user_text(prompt) if prompt.strip() else prompt
    typed = redact(prompts.strip_pastes(own))
    tickets = repos.tickets_from(cfg).find(typed)
    nosave = prompts.has_nosave(own)
    store_prompt(cfg, seg, payload.get("prompt_id"), typed, tickets, nosave)
    preset = cfg.get_str("language", "preset")
    cue = CUE_LINE if fold.has_cue(typed, cfg.language_list("correction_cues"), preset) else ""
    cap = cfg.get_int("caps", "hook_output_chars")
    budget = cap - len(cue) - 2
    view = DiskVault(cfg)
    before = set(seg.packs)
    delivered = delivery.deliver_packs(cfg, view, seg, [("ticket", k) for k in tickets], budget)
    topic_text = topicsio.for_text(cfg, sid, None, typed, budget - len(delivered) - 2, view)
    status.update(cfg, seg)
    shadowio.observe(cfg, seg, view, typed, own, nosave, tickets, before, delivered)
    text = "\n\n".join(t for t in (cue, delivered, topic_text) if t)
    return output(cfg, "UserPromptSubmit", inject.trim(text, cap, "the vault"))
