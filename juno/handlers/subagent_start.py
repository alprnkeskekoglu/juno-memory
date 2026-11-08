from typing import Any

from juno import config
from juno.adapters import ledgerio, repos, topicsio
from juno.adapters.vaultview import DiskVault
from juno.core import packs
from juno.core.packs import Caps, Wanted
from juno.handlers.common import output, payload_cwd


def subagent_wanted(cfg: config.Config, sid: str, cwd: str) -> Wanted:
    seg = ledgerio.fold(cfg, sid)
    found = repos.repos_for(cfg, [cwd]) if cwd else {}
    if seg.last_repo and seg.last_repo not in found:
        found[seg.last_repo] = seg.repos.get(seg.last_repo)
    wanted = packs.wanted_for(found)
    ticket = seg.current_ticket()
    if ticket:
        wanted.insert(0, ("ticket", ticket))
    return list(dict.fromkeys(wanted))


def handle(payload: dict[str, Any], cfg: config.Config) -> dict[str, Any] | None:
    sid = str(payload.get("session_id") or "")
    agent = payload.get("agent_id")
    view = DiskVault(cfg)

    def topic_lines(budget: int) -> list[str]:
        return topicsio.for_subagent(cfg, sid, agent, budget)

    wanted = subagent_wanted(cfg, sid, payload_cwd(payload))
    text = packs.subagent_block(view, Caps.from_config(cfg), wanted, topic_lines)
    return output(cfg, "SubagentStart", text)
