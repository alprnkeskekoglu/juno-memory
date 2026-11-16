import contextlib
import hashlib
import json
import os
from typing import Any

from juno import config
from juno.adapters import fs, ledgerio
from juno.core import fold, voicecollect, voicemeasure
from juno.core.ledger import Record, Segment
from juno.core.redact import redact

STATE_FILE = "voice.jsonl"
NAMES_FILE = "voice-names.json"
NAME_DIRS = ("projects", "handoffs")
MAX_NAME_FILES = 300
MAX_NAMES = 2000
VAULT_LOCK = "vault.lock"
MAX_SAMPLES = 400
MAX_FIXES = 100
TEXT_BYTES = 1200
DRAFT_BYTES = 600
QUOTE_BYTES = 400
FIX_DRAFT_BYTES = 1000

Row = dict[str, Any]


def enabled(cfg: config.Config) -> bool:
    return cfg.get_bool("voice", "enabled")


def quiet(seg: Segment) -> bool:
    return seg.private or seg.nosave


def state_path(cfg: config.Config) -> str:
    return os.path.join(cfg.state, STATE_FILE)


def read_rows(cfg: config.Config) -> list[Row]:
    data = fs.read_bytes(state_path(cfg))
    rows: list[Row] = []
    for line in (data or b"").decode("utf-8", "replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("k") in ("sample", "fix"):
            rows.append(row)
    return rows


def write_rows(cfg: config.Config, rows: list[Row]) -> None:
    fs.ensure_private_dir(cfg.state)
    text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    fs.atomic_write(state_path(cfg), text.encode(), 0o600)


def names_path(cfg: config.Config) -> str:
    return os.path.join(cfg.state, NAMES_FILE)


def read_names(cfg: config.Config) -> set[str]:
    try:
        found = json.loads((fs.read_bytes(names_path(cfg)) or b"[]").decode("utf-8", "replace"))
    except ValueError:
        return set()
    return {n for n in found if isinstance(n, str)} if isinstance(found, list) else set()


def vault_names(cfg: config.Config) -> set[str]:
    """Names in project profiles and handoff cards; read on every use, never stored."""
    found: set[str] = set()
    for sub in NAME_DIRS:
        folder = os.path.join(cfg.vault, sub)
        try:
            files = sorted(f for f in os.listdir(folder) if f.endswith(".md"))
        except OSError:
            continue
        for name in files[:MAX_NAME_FILES]:
            data = fs.read_bytes(os.path.join(folder, name))
            found |= voicemeasure.name_tokens((data or b"").decode("utf-8", "replace"))
    return found


def known_names(cfg: config.Config, rows: list[Row]) -> set[str]:
    """Casefolded names a sample must not contain to be an example.

    From the vault profiles and cards, the names directory in the state dir (names seen in the
    user's own prompts), the sample texts and `[privacy] names`. Kept in memory and in the state
    dir only: never written to the vault, a pack or a profile."""
    found = vault_names(cfg) | read_names(cfg)
    for row in rows:
        if row.get("k") == "sample":
            found |= voicemeasure.name_tokens(str(row.get("text") or ""))
    found |= {n.casefold() for n in cfg.get_list("privacy", "names")}
    return found


def remember_names(cfg: config.Config, seg: Segment) -> None:
    found = read_names(cfg)
    for text in seg.prompts.values():
        found |= voicemeasure.name_tokens(text)
    if found == read_names(cfg):
        return
    fs.ensure_private_dir(cfg.state)
    kept = sorted(found)[:MAX_NAMES]
    fs.atomic_write(names_path(cfg), json.dumps(kept, ensure_ascii=False).encode(), 0o600)


def typed_since_draft(seg: Segment) -> list[str]:
    prompts = seg.prompts
    return [prompts[pid] for pid in seg.since_stop if pid in prompts]


def collect(
    cfg: config.Config, seg: Segment, payload: dict[str, Any], tool: object, tool_in: dict[str, Any]
) -> None:
    """PostToolUse: one `voice` ledger event per outward write of the main session."""
    if payload.get("agent_id") or not enabled(cfg) or quiet(seg):
        return
    if not voicecollect.matches_outward(tool, tool_in, cfg.get_list("voice", "outward_tools")):
        return
    text = redact(voicecollect.post_text(tool, tool_in))
    if not text:
        return
    by = voicecollect.authorship(text, seg.last_msg, typed_since_draft(seg))
    rec: Record = {
        "ev": "voice",
        "reg": seg.voice_reg or "voice",
        "kind": voicecollect.target_kind(tool, tool_in),
        "by": by,
        "text": voicecollect.clip(text, TEXT_BYTES),
    }
    if seg.last_msg and by != "user":
        rec["draft"] = voicecollect.clip(seg.last_msg, DRAFT_BYTES)
    ledgerio.record(cfg, seg, rec)


def block(cfg: config.Config, seg: Segment, reg: str) -> str:
    rows = read_rows(cfg)
    minimum = cfg.get_int("voice", "min_samples")
    prefixes = cfg.privacy_prefixes()
    samples = [r for r in rows if r["k"] == "sample"]
    profile = voicemeasure.build(samples, reg, minimum, prefixes, known_names(cfg, rows))
    fixes = [r for r in rows if r["k"] == "fix"] + seg.voice_fixes
    mine = voicemeasure.clean_fixes(fixes, reg, prefixes)
    return voicemeasure.drafting_block(
        reg, profile, mine, minimum, cfg.get_int("voice", "budget_chars")
    )


def on_prompt(cfg: config.Config, seg: Segment, typed: str, nosave: bool) -> str:
    """UserPromptSubmit: record a draft request or a complaint; return the drafting block."""
    if not enabled(cfg):
        return ""
    preset = cfg.get_str("language", "preset")
    hold = quiet(seg) or nosave
    asked = seg.voice_reg is not None
    if (
        asked
        and seg.last_msg
        and not hold
        and fold.has_cue(typed, cfg.voice_list("critique_cues"), preset)
    ):
        ledgerio.record(
            cfg,
            seg,
            {
                "ev": "voice_fix",
                "reg": seg.voice_reg,
                "quote": voicecollect.clip(typed, QUOTE_BYTES),
                "draft": voicecollect.clip(seg.last_msg, FIX_DRAFT_BYTES),
            },
        )
    formal = fold.has_cue(typed, cfg.voice_list("formal_cues"), preset)
    own = fold.has_cue(typed, cfg.voice_list("voice_cues"), preset)
    if not (
        fold.has_cue(typed, cfg.voice_list("draft_cues"), preset) or (asked and (formal or own))
    ):
        return ""
    reg = "formal" if formal else "voice"
    if not hold:
        ledgerio.record(cfg, seg, {"ev": "voice_ask", "reg": reg})
    return block(cfg, seg, reg)


def pre_tool(cfg: config.Config, payload: dict[str, Any], tool_in: dict[str, Any]) -> str:
    """PreToolUse on an outward tool: a non-blocking line on what is off, and the profile once."""
    tool = payload.get("tool_name")
    sid = payload.get("session_id")
    if payload.get("agent_id") or not enabled(cfg) or not ledgerio.exists(cfg, sid):
        return ""
    if not voicecollect.matches_outward(tool, tool_in, cfg.get_list("voice", "outward_tools")):
        return ""
    text = redact(voicecollect.post_text(tool, tool_in))
    if not text:
        return ""
    seg = ledgerio.fold(cfg, sid)
    reg = seg.voice_reg or "voice"
    parts: list[str] = []
    typed = typed_since_draft(seg)
    if voicecollect.authorship(text, seg.last_msg, typed) != "user":
        rows = read_rows(cfg)
        samples = [r for r in rows if r["k"] == "sample"]
        minimum = cfg.get_int("voice", "min_samples")
        profile = voicemeasure.build(
            samples, reg, minimum, cfg.privacy_prefixes(), known_names(cfg, rows)
        )
        found = voicemeasure.check(text, profile)
        if found:
            parts.append(
                f"Juno voice check (not blocking; register {reg}): this text has "
                f"{', '.join(found)} against the user's own writing. Rewrite it closer to the "
                "register, unless the user asked for it as it is."
            )
    if seg.voice_reg is None:
        if not quiet(seg):
            ledgerio.record(cfg, seg, {"ev": "voice_ask", "reg": reg})
        parts.append(block(cfg, seg, reg))
    return "\n\n".join(parts)


def sample_id(sid: str, rec: Record, kind: str) -> str:
    raw = f"{sid}|{kind}|{rec.get('t')}|{rec.get('text') or rec.get('quote')}"
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:16]


def rows_of(seg: Segment) -> list[Row]:
    rows: list[Row] = []
    for rec in seg.voice_samples:
        rows.append(
            {
                "k": "sample",
                "id": sample_id(seg.sid, rec, "sample"),
                "t": rec.get("t"),
                "sid": seg.sid,
                "reg": rec.get("reg"),
                "kind": rec.get("kind"),
                "by": rec.get("by"),
                "text": rec.get("text"),
            }
        )
    for rec in seg.voice_fixes:
        rows.append(
            {
                "k": "fix",
                "id": sample_id(seg.sid, rec, "fix"),
                "t": rec.get("t"),
                "sid": seg.sid,
                "reg": rec.get("reg"),
                "quote": rec.get("quote"),
                "draft": rec.get("draft"),
            }
        )
    return rows


def pruned(rows: list[Row]) -> list[Row]:
    samples = [r for r in rows if r["k"] == "sample"][-MAX_SAMPLES:]
    fixes = [r for r in rows if r["k"] == "fix"][-MAX_FIXES:]
    keep = {id(r) for r in samples + fixes}
    return [r for r in rows if id(r) in keep]


def write_profile(cfg: config.Config, rows: list[Row]) -> bool:
    minimum = cfg.get_int("voice", "min_samples")
    prefixes = cfg.privacy_prefixes()
    samples = [r for r in rows if r["k"] == "sample"]
    names = known_names(cfg, rows)
    profiles = [
        voicemeasure.build(samples, reg, minimum, prefixes, names) for reg in voicemeasure.REGISTERS
    ]
    if not any(p.ready for p in profiles):
        return False
    fixes = [r for r in rows if r["k"] == "fix"]
    text = voicemeasure.file_text(profiles, fixes, prefixes)
    target = os.path.join(cfg.vault, "self", "voice.md")
    with contextlib.suppress(TimeoutError), fs.locked(os.path.join(cfg.state, VAULT_LOCK), 0.5):
        fs.atomic_write(target, text.encode(), 0o600)
        return True
    return False


def on_session_end(cfg: config.Config, sid: object) -> None:
    """Move the segment's samples into the state file and rebuild `self/voice.md`."""
    if not enabled(cfg) or ledgerio.ledger_path(cfg, sid) is None:
        return
    seg = ledgerio.fold(cfg, sid)
    rows = read_rows(cfg)
    if quiet(seg):
        kept = [r for r in rows if r.get("sid") != seg.sid]
        if len(kept) != len(rows):
            write_rows(cfg, kept)
        return
    remember_names(cfg, seg)
    known = {r.get("id") for r in rows}
    fresh = [r for r in rows_of(seg) if r["id"] not in known]
    if not fresh:
        return
    merged = pruned(rows + fresh)
    write_rows(cfg, merged)
    if any(r["k"] == "sample" for r in fresh):
        write_profile(cfg, merged)
