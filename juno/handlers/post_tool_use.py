import contextlib
import os
from typing import Any

from juno import config
from juno.adapters import agentcalls, armsio, asklog, ledgerio, repos, status, store, usageio
from juno.adapters.vaultview import DiskVault
from juno.core import packs, toolrec, vaultfmt
from juno.core.ledger import Record, Segment
from juno.core.redact import chunks
from juno.handlers import delivery
from juno.handlers.common import output, payload_cwd, tool_input

PATH_KEYS = ("file_path", "path", "notebook_path")
READ_TOOLS = ("Read", "Edit", "Write", "NotebookEdit")
PRIVATE_SCAN_BYTES = 2048


def tool_paths(cfg: config.Config, tool: object, tool_in: dict[str, Any], cwd: str) -> list[str]:
    found: list[str] = []
    for key in PATH_KEYS:
        value = tool_in.get(key)
        if isinstance(value, str) and value:
            found.append(store.resolve(value, cwd))
    command = tool_in.get("command")
    if tool == "Bash" and isinstance(command, str):
        home = cfg.env.get("HOME") or os.path.expanduser("~")
        roots = cfg.repo_roots()
        for candidate, forced in toolrec.bash_path_candidates(toolrec.shell_tokens(command), home):
            path = store.resolve(candidate, cwd)
            if forced or any(store.is_within(path, root) for root in roots):
                found.append(path)
    return found


def mark_write(
    cfg: config.Config, rec: Record, real: str, found: dict[str, str | None], sid: object
) -> None:
    parts = store.vault_rel(cfg, real)
    if parts is not None:
        if (
            len(parts) == 2
            and parts[0].casefold() == "handoffs"
            and parts[1].endswith(".md")
            and isinstance(sid, str)
        ):
            from juno.adapters.stubs import has_card_for

            if has_card_for(real, sid[:8]):
                rec["card"] = "handoffs/" + parts[1]
        return
    hit = repos.detect_repo(cfg, real)
    if hit:
        ticket = found[hit[0]] if hit[0] in found else repos.repo_ticket(cfg, hit[1])
        rec["edits"] = [[hit[0], ticket]]


def tool_record(
    cfg: config.Config, tool: object, tool_in: dict[str, Any], payload: dict[str, Any], cwd: str
) -> tuple[Record, dict[str, str | None]]:
    rec: Record = {
        "ev": "tool",
        "name": str(tool or "-")[:80],
        "agent": bool(payload.get("agent_id")),
    }
    found = repos.repos_for(cfg, [cwd, *tool_paths(cfg, tool, tool_in, cwd)])
    rec["repos"] = [[repo, ticket] for repo, ticket in found.items()]
    target = toolrec.write_target(tool_in) if tool in toolrec.WRITE_TOOLS else None
    if target:
        mark_write(cfg, rec, store.resolve(target, cwd), found, payload.get("session_id"))
    if not rec["agent"] and isinstance(tool, str) and tool.startswith("mcp__"):
        keys = toolrec.tool_ticket_keys(
            tool, tool_in, payload.get("tool_response"), repos.tickets_from(cfg)
        )
        if keys:
            rec["keys"] = keys
    if tool == "Bash":
        response = payload.get("tool_response")
        stdout = response.get("stdout") if isinstance(response, dict) else ""
        command = tool_in.get("command")
        if toolrec.is_boundary(
            command if isinstance(command, str) else "", stdout if isinstance(stdout, str) else ""
        ):
            rec["boundary"] = True
    return rec, found


def touches_private_file(
    cfg: config.Config, tool: object, tool_in: dict[str, Any], cwd: str
) -> bool:
    if tool not in READ_TOOLS:
        return False
    for key in PATH_KEYS:
        value = tool_in.get(key)
        if not isinstance(value, str) or not value:
            continue
        real = store.resolve(value, cwd)
        parts = store.vault_rel(cfg, real)
        if parts is None or not real.endswith(".md"):
            continue
        try:
            with open(real, encoding="utf-8", errors="replace") as handle:
                head = handle.read(PRIVATE_SCAN_BYTES)
        except OSError:
            continue
        fields, _ = vaultfmt.parse_frontmatter(head + "\n---")
        if vaultfmt.is_private(fields):
            return True
    return False


def record_answers(cfg: config.Config, seg: Segment, payload: dict[str, Any]) -> None:
    tid = str(payload.get("tool_use_id") or "")[:64]
    if not tid or not seg.ask_clean(tid):
        return
    texts = toolrec.chosen_texts(tool_input(payload), payload.get("tool_response"))
    for k, text in enumerate(texts):
        for i, piece in enumerate(chunks(text)):
            ledgerio.record(
                cfg, seg, {"ev": "answer", "tid": tid, "k": k, "part": i, "text": piece}
            )
    rows = toolrec.ask_rows(tool_input(payload), payload.get("tool_response"))
    with contextlib.suppress(OSError):
        running = armsio.arms_for(cfg, seg.sid)
        if running:
            rows = [{**row, "arms": running} for row in rows]
        asklog.append(cfg.state, seg.sid, tid, rows)


def handle(payload: dict[str, Any], cfg: config.Config) -> dict[str, Any] | None:
    agentcalls.clear(cfg, payload)
    sid = payload.get("session_id")
    cwd = payload_cwd(payload)
    tool = payload.get("tool_name")
    tool_in = tool_input(payload)
    rec, found = tool_record(cfg, tool, tool_in, payload, cwd)
    seg = ledgerio.fold(cfg, sid)
    ledgerio.record(cfg, seg, rec)
    with contextlib.suppress(OSError):
        usageio.record_touch(cfg, sid, tool, tool_in, cwd)
    if not seg.private and touches_private_file(cfg, tool, tool_in, cwd):
        ledgerio.record(cfg, seg, {"ev": "private", "why": "visibility"})
    if payload.get("agent_id"):
        return None
    if tool == toolrec.ASK_TOOL:
        record_answers(cfg, seg, payload)
    view = DiskVault(cfg)
    text = delivery.deliver_packs(
        cfg, view, seg, packs.wanted_for(found), cfg.get_int("caps", "hook_output_chars")
    )
    status.update(cfg, seg)
    return output(cfg, "PostToolUse", text)
