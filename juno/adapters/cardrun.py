import contextlib
import json
import os
import time
from collections.abc import Callable, Iterator
from typing import Any

from juno import config
from juno.adapters import (
    armseg,
    armsio,
    cardjob,
    cardwrite,
    fs,
    learnio,
    learnkinds,
    ledgerio,
    modelcall,
    store,
    threadwatch,
)
from juno.core import cardfmt, digest, extract, routing, selfmodel
from juno.core.ledger import Segment

TRANSCRIPT_BYTES = 8 * 1024 * 1024
RUN_DAYS = 30
SECTION_KEYS = ("card", *learnkinds.KEYS)

Call = Callable[[config.Config, str, float], tuple[str, float]]


def none_sections() -> dict[str, str]:
    return dict.fromkeys(SECTION_KEYS, "none")


@contextlib.contextmanager
def segment_lock(cfg: config.Config, sid: str) -> Iterator[bool]:
    import fcntl

    found = cardjob.job_paths(cfg, sid)
    if found is None:
        yield False
        return
    fs.ensure_private_dir(cardjob.cards_dir(cfg))
    fd = os.open(found[1], os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def append_run(cfg: config.Config, row: dict[str, Any]) -> None:
    fs.append_log(
        os.path.join(cardjob.cards_dir(cfg), cardjob.RUNS_FILE),
        json.dumps(row, ensure_ascii=False, sort_keys=True),
        1 << 30,
    )


def prune_runs(cfg: config.Config, now: float | None = None) -> None:
    stamp = now or time.time()
    rows = cardjob.read_runs(cfg)
    kept = [r for r in rows if stamp - r["t"] <= RUN_DAYS * 86400]
    if len(kept) != len(rows):
        text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in kept)
        fs.atomic_write(
            os.path.join(cardjob.cards_dir(cfg), cardjob.RUNS_FILE), text.encode("utf-8"), 0o600
        )


def transcript_lines(path: object) -> list[str]:
    if not isinstance(path, str) or not path.endswith(".jsonl") or not os.path.isabs(path):
        return []
    try:
        if not os.path.isfile(path):
            return []
        with open(path, "rb") as handle:
            handle.seek(max(0, os.path.getsize(path) - TRANSCRIPT_BYTES))
            data = handle.read(TRANSCRIPT_BYTES)
    except OSError:
        return []
    return data.decode("utf-8", "replace").splitlines()


def build(
    cfg: config.Config, seg: Segment, targets: list[routing.Target], transcript: object
) -> str:
    facts = digest.facts_from_lines(transcript_lines(transcript), os.path.expanduser("~"))
    return digest.build_digest(
        seg,
        facts,
        cardwrite.existing_card(cfg, targets, seg.sid[:8]),
        cfg.get_int("handoff", "digest_chars"),
        cfg.language_list("user_cues"),
        cfg.language_list("self_cues"),
    )


def assistant_turns(
    cfg: config.Config, transcript: object
) -> tuple[list[tuple[int, str]], int] | None:
    if "self" not in armsio.running(cfg):
        return None
    facts = digest.facts_from_lines(transcript_lines(transcript), os.path.expanduser("~"))
    shown, total = selfmodel.label_turns([e.text for e in facts.events if e.kind == "assistant"])
    return (shown, total) if total else None


def prompt_for(
    cfg: config.Config,
    seg: Segment,
    targets: list[routing.Target],
    text: str,
    stamp: str,
    naming: bool,
    extracting: bool,
    turns: tuple[list[tuple[int, str]], int] | None = None,
) -> str:
    target = targets[0]
    return cardfmt.build_prompt(
        name="Juno",
        seg8=seg.sid[:8],
        key=str(target["key"]),
        repos=list(target["repos"]),
        tickets=list(target["tickets"]),
        format_text=cardfmt.format_block(store.read_text(os.path.join(cfg.vault, "core.md"))),
        stamp=stamp,
        digest=text,
        fence="data-" + os.urandom(6).hex(),
        naming=cardwrite.handoff_names(cfg) if naming else None,
        extract_lines=learnio.instructions(cfg, seg, text, turns) if extracting else [],
        state_text=learnio.vault_state(cfg, seg, text, turns) if extracting else "",
    )


def write_valid_card(
    cfg: config.Config,
    seg: Segment,
    targets: list[routing.Target],
    card_text: str,
    stamp: str,
    naming: bool,
    out: dict[str, Any],
) -> list[str]:
    chosen, body = cardfmt.split_file_line(card_text)
    body, out["links_unlinked"] = cardfmt.fix_links(body)
    body = cardfmt.normalise_heading(body, seg.sid[:8], str(targets[0]["key"]), stamp)
    prefixes = cfg.privacy_prefixes()
    key = str(targets[0]["key"])
    try:
        body = cardfmt.validate_card(body, seg.sid[:8], key, stamp, prefixes)
    except cardfmt.Failed as exc:
        masked, counts = cardfmt.mask_private(body, seg.sid[:8], str(exc), prefixes)
        if not counts:
            raise
        body = cardfmt.validate_card(masked, seg.sid[:8], key, stamp, prefixes)
        out["masked"] = counts
    if naming:
        slug = cardfmt.valid_slug(chosen, cardwrite.reserved_names(cfg))
        targets = [dict(targets[0], path=f"handoffs/{slug or 'misc'}.md")]
        out["file_choice"] = chosen
    paths = cardwrite.write_card(cfg, seg, targets, body)
    rels = [store.rel(cfg, p) for p in paths]
    ledgerio.append(cfg, seg.sid, {"ev": "card", "path": ",".join(rels), "job": True})
    return rels


def make_card(
    cfg: config.Config, sid: str, job: dict[str, Any], call: Call = modelcall.call_model
) -> dict[str, Any]:
    seg = ledgerio.fold(cfg, sid)
    reason: str | None
    if seg.private:
        reason = "private"
    elif seg.nosave:
        reason = "nosave"
    else:
        reason = None if seg.dirty else "card current"
    if reason:
        return {"status": "skipped", "error": reason, "sections": none_sections()}
    targets = routing.card_targets(seg)
    naming = not seg.card_files and [t["path"] for t in targets] == ["handoffs/misc.md"]
    stamp = routing.local_stamp()
    extracting = learnio.enabled(cfg)
    text = build(cfg, seg, targets, job.get("transcript"))
    turns = assistant_turns(cfg, job.get("transcript")) if extracting else None
    prompt = prompt_for(cfg, seg, targets, text, stamp, naming, extracting, turns)
    answer, cost = call(cfg, prompt, cardfmt.number(job.get("max_budget_usd"), 0.0))
    card_text, raw = extract.split_extract(answer) if extracting else (answer, None)
    out: dict[str, Any] = {"status": "ok", "cost_usd": round(cost, 4), "files": [], "error": None}
    sections = none_sections()
    try:
        out["files"] = write_valid_card(cfg, seg, targets, card_text, stamp, naming, out)
        sections["card"] = "ok"
    except cardfmt.Failed as exc:
        out.update(status="failed", error=str(exc)[:300])
        sections["card"] = f"dropped:{str(exc)[:80]}"
    if extracting:
        result, extracted = learnio.apply(
            cfg, seg, raw, text, out["files"] or seg.card_files, turns=turns
        )
        out.update(result)
        sections.update(extracted)
    out["sections"] = sections
    return out


def run_locked(
    cfg: config.Config, sid: str, job: dict[str, Any], call: Call = modelcall.call_model
) -> int:
    start = time.time()
    job.update(status="running", pid=os.getpid(), started=start)
    job.pop("token", None)
    cardjob.write_job(cfg, sid, job)
    row: dict[str, Any] = {
        "t": start,
        "sid": sid,
        "status": "failed",
        "cost_usd": 0.0,
        "error": None,
        "files": [],
        "sections": none_sections(),
    }
    arms = armsio.arms_for(cfg, sid)
    if arms:
        row["arms"] = arms
    try:
        row.update(make_card(cfg, sid, job, call))
    except cardfmt.Failed as exc:
        row.update(error=str(exc)[:300], cost_usd=round(exc.cost, 4))
        row["sections"] = {**none_sections(), "card": f"dropped:{str(exc)[:80]}"}
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        store.log_error(cfg, "card-run", exc)
    row["thread_scan"] = scan_threads(cfg)
    row["seconds"] = round(time.time() - start, 1)
    append_run(cfg, row)
    try:
        armseg.sweep(cfg)
    except Exception as exc:
        store.log_error(cfg, "arm-segments", exc)
    job.update(
        status="done" if row["status"] == "ok" else row["status"],
        finished=time.time(),
        error=row["error"],
        files=row["files"],
        cost_usd=row["cost_usd"],
    )
    if row.get("masked"):
        job["masked"] = row["masked"]
    else:
        job.pop("masked", None)
    cardjob.write_job(cfg, sid, job)
    prune_runs(cfg)
    return 0 if row["status"] in ("ok", "skipped") else 1


def scan_threads(cfg: config.Config) -> dict[str, Any]:
    try:
        return dict(threadwatch.scan(cfg))
    except Exception as exc:
        store.log_error(cfg, "thread-scan", exc)
        return {"error": type(exc).__name__}


def run(cfg: config.Config, sid: str, token: str, call: Call = modelcall.call_model) -> int:
    import hmac

    job = cardjob.read_job(cfg, sid)
    if not job.get("token") or not hmac.compare_digest(str(job["token"]), token):
        return 1
    with segment_lock(cfg, sid) as held:
        return run_locked(cfg, sid, job, call) if held else 1
