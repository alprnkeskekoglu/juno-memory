"""A `decisions/<x>.md` file that a profile or a ticket owns belongs in the owner's file (#31)."""

from juno.core import decisionmove
from juno.core.doctor_vault import Check

SHOWN = 8
MIGRATE_HINT = "juno migrate --decisions"


def decision_checks(plan: decisionmove.Plan, acks: int = 0) -> list[Check]:
    out: list[Check] = [
        ("WARN", "decisions", f"{m.source} belongs in {m.owner.rel}; `{MIGRATE_HINT}` moves it")
        for m in plan.moves[:SHOWN]
    ]
    if len(plan.moves) > SHOWN:
        out.append(("WARN", "decisions", f"{len(plan.moves) - SHOWN} more files to move"))
    out += [
        ("WARN", "decisions", f"{d.source} has no current lines; `{MIGRATE_HINT}` removes it")
        for d in plan.drops[:SHOWN]
    ]
    if len(plan.drops) > SHOWN:
        out.append(("WARN", "decisions", f"{len(plan.drops) - SHOWN} more files to remove"))
    if acks:
        out.append(
            (
                "WARN",
                "decisions",
                f"{acks} privacy acknowledgements point at a moved decisions file; "
                f"`{MIGRATE_HINT}` moves them",
            )
        )
    out += [
        ("INFO", "decisions", f"{s.source} stays where it is: {s.reason}")
        for s in plan.skips[:SHOWN]
    ]
    if len(plan.skips) > SHOWN:
        out.append(("INFO", "decisions", f"{len(plan.skips) - SHOWN} more files stay"))
    return out or [("PASS", "decisions", "no decisions file waits for its owner")]
