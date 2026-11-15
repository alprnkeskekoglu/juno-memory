"""A `decisions/<x>.md` file that a profile or a ticket owns belongs in the owner's file (#31)."""

from juno.core import decisionmove
from juno.core.doctor_vault import Check

SHOWN = 8
MIGRATE_HINT = "juno migrate --decisions"


def decision_checks(plan: decisionmove.Plan) -> list[Check]:
    out: list[Check] = [
        ("WARN", "decisions", f"{m.source} belongs in {m.owner.rel}; `{MIGRATE_HINT}` moves it")
        for m in plan.moves[:SHOWN]
    ]
    if len(plan.moves) > SHOWN:
        out.append(("WARN", "decisions", f"{len(plan.moves) - SHOWN} more files to move"))
    out += [
        ("INFO", "decisions", f"{s.source} stays where it is: {s.reason}")
        for s in plan.skips[:SHOWN]
    ]
    if len(plan.skips) > SHOWN:
        out.append(("INFO", "decisions", f"{len(plan.skips) - SHOWN} more files stay"))
    return out or [("PASS", "decisions", "no decisions file waits for its owner")]
