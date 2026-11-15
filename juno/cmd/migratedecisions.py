from juno import config
from juno.adapters import decisionmoveio
from juno.cmd.common import say


def move_decisions(cfg: config.Config, dry_run: bool) -> int:
    """Move each decisions/<x>.md that a profile or a ticket owns into the owner's file."""
    plan = decisionmoveio.plan_for(cfg)
    if (plan.moves or plan.drops) and not dry_run and decisionmoveio.apply(cfg, plan, say):
        return 1
    verb = "would move" if dry_run else "moved"
    for move in plan.moves:
        extra = f", {move.history} to {move.archive_rel}" if move.history else ""
        say(f"decisions: {verb} {move.source} to {move.owner.rel} ({move.lines} lines{extra})")
    for drop in plan.drops:
        verb = "would remove" if dry_run else "removed"
        extra = f", {drop.history} to {drop.archive_rel}" if drop.history else ""
        say(f"decisions: {verb} {drop.source} (no current lines{extra})")
    for skip in plan.skips:
        say(f"decisions: {skip.source} stays: {skip.reason}")
    if decisionmoveio.carry_acks(cfg, plan, dry_run, say):
        return 1
    if not plan.moves and not plan.drops:
        say("decisions: nothing to move")
        return 0
    links = decisionmoveio.count_links(cfg, plan)
    if links:
        say(f"decisions: {links} [[decisions/...]] path links point at moved files; not rewritten")
    return 0
