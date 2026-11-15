"""Decision files in every shape the owner move meets (made-up content).

`build(vault)` writes files only; it configures nothing. A test that wants tickets routed must
set `[jira] prefixes = ["ABC"]` (the key prefix of the ticket files below). Without it the
ticket-shaped files are ownerless topics and must stay where they are.

Shapes (see the constants):
- ABC-1 .. ABC-17 have a task file (`tasks/<KEY>.md`); ABC-1 .. ABC-5 also have `## Notes`
  lines, ABC-3 an existing `## Decisions` line that repeats one legacy line exactly.
  ABC-1 and ABC-2 also have `## History` lines in their decision file.
- ABC-18 .. ABC-30 (13 keys) have a decision file and no task file.
- ABC-31 (LINELESS) has a decision file with no line and no task file; ABC-32 (LINELESS_HISTORY)
  the same with one History line. Neither is in TICKETS: the move creates no task file for them.
- alpha: profile with a `## Decisions` line the legacy file repeats exactly (ALPHA_DUPLICATE),
  a near-duplicate pair in the legacy file (ALPHA_NEAR_A/B, both must survive) and History.
- beta: profile without `## Decisions`, legacy file with Current and History.
- app-2: profile `projects/app-2.md`, legacy file `decisions/APP-2.md` (old uppercasing bug).
- ownerless topics `decisions/payments-design.md`, `decisions/release-process.md`, and
  `decisions/XYZ-9.md` (a ticket shape whose prefix is not configured).
- gamma: profile plus a private (hidden) `decisions/gamma.md`.
- delta: profile plus `decisions/delta.md` with stray body text outside Current/History.

Decision files get SOURCE_MTIME, existing owner files OWNER_MTIME.
"""

import os

from tests.support.legacy_vault import write

PREFIX = "ABC"
WITH_TASK = [f"ABC-{i}" for i in range(1, 18)]
WITHOUT_TASK = [f"ABC-{i}" for i in range(18, 31)]
TICKETS = WITH_TASK + WITHOUT_TASK
WITH_NOTES = WITH_TASK[:5]
WITH_HISTORY = WITH_TASK[:2]
LINELESS = "ABC-31"
LINELESS_HISTORY = "ABC-32"
EXISTING_DECISIONS_TICKET = "ABC-3"
OWNERLESS = ("payments-design", "release-process", "XYZ-9")
SOURCE_MTIME = 1_700_000_000
OWNER_MTIME = 1_650_000_000
DAY = "2026-11-01"
ALPHA_DUPLICATE = "- Pin the base image to a digest (user pid00001, 2026-10-01)"
ALPHA_NEAR_A = "- Retry uploads with backoff (user pid00001, 2026-10-02)"
ALPHA_NEAR_B = "- Retry uploads with backoff. (user pid00001, 2026-10-02)"
APP2_LINE = "- Keep the queue name in one place (user pid00001, 2026-10-03)"
STRAY_TEXT = "Notes from a call that belong to no section."


def line(text: str, day: str = DAY) -> str:
    return f"- {text} (user pid00001, {day})"


def decision_file(topic: str, current: list[str], history: list[str] | None = None) -> str:
    head = f"---\ntopic: {topic}\nupdated: {DAY}\nvalid_from: {DAY}\n---\n"
    past = "\n".join(history or [])
    return head + "## Current\n\n" + "\n".join(current) + "\n\n## History\n" + past + "\n"


def task_file(key: str, extra: str = "") -> str:
    return (
        f"---\njira: {key}\ntitle: Made-up work {key}\nstatus: active\ncreated: {DAY}\n"
        f"updated: {DAY}\n---\n\n{extra}## Notes\n- note about {key}\n"
        if key in WITH_NOTES
        else f"---\njira: {key}\ntitle: Made-up work {key}\nstatus: active\ncreated: {DAY}\n"
        f"updated: {DAY}\n---\n\n{extra}"
    )


def put(vault: str, rel: str, text: str, mtime: int) -> None:
    write(vault, rel, text)
    os.utime(os.path.join(vault, *rel.split("/")), (mtime, mtime))


def build(vault: str) -> None:
    """Write the decision shapes into `vault`."""
    for key in TICKETS:
        current = [line(f"first choice for {key}"), line(f"second choice for {key}")]
        history = [line(f"old choice for {key}", "2026-09-01")] if key in WITH_HISTORY else []
        if key == EXISTING_DECISIONS_TICKET:
            current.append(line("a choice already in the task file"))
        put(vault, f"decisions/{key}.md", decision_file(key, current, history), SOURCE_MTIME)
    put(vault, f"decisions/{LINELESS}.md", decision_file(LINELESS, []), SOURCE_MTIME)
    old = [line("old choice for the lineless ticket", "2026-09-01")]
    put(
        vault,
        f"decisions/{LINELESS_HISTORY}.md",
        decision_file(LINELESS_HISTORY, [], old),
        SOURCE_MTIME,
    )
    for key in WITH_TASK:
        extra = ""
        if key == EXISTING_DECISIONS_TICKET:
            extra = f"## Decisions\n\n{line('a choice already in the task file')}\n\n"
        put(vault, f"tasks/{key}.md", task_file(key, extra), OWNER_MTIME)
    # alpha: exact duplicate, near-duplicate pair, History
    put(
        vault,
        "projects/alpha.md",
        f"# alpha\n\n## Decisions\n\n{ALPHA_DUPLICATE}\n- Only in the profile (user pid00001, 2026-10-04)\n\n"
        "## Gotchas\n- the upload job needs the VPN\n",
        OWNER_MTIME,
    )
    alpha_current = [ALPHA_DUPLICATE, ALPHA_NEAR_A, ALPHA_NEAR_B, line("Only in the legacy file")]
    alpha_history = [line("Used a floating base image", "2026-09-01")]
    put(
        vault,
        "decisions/alpha.md",
        decision_file("alpha", alpha_current, alpha_history),
        SOURCE_MTIME,
    )
    # beta: no Decisions section yet
    put(vault, "projects/beta.md", "# beta\n\n## Gotchas\n- slow cold start\n", OWNER_MTIME)
    beta_history = [line("Dropped the cache layer", "2026-08-01")]
    put(
        vault,
        "decisions/beta.md",
        decision_file("beta", [line("Use one queue")], beta_history),
        SOURCE_MTIME,
    )
    # app-2: the profile is lowercase, the legacy file was written uppercased
    put(vault, "projects/app-2.md", "# app-2\n\n## Gotchas\n- needs the broker\n", OWNER_MTIME)
    put(vault, "decisions/APP-2.md", decision_file("APP-2", [APP2_LINE]), SOURCE_MTIME)
    # ownerless topics
    for topic in OWNERLESS:
        put(
            vault,
            f"decisions/{topic}.md",
            decision_file(topic, [line(f"choice in {topic}")]),
            SOURCE_MTIME,
        )
    # hidden: a private legacy file with an owner
    put(vault, "projects/gamma.md", "# gamma\n", OWNER_MTIME)
    private = decision_file("gamma", [line("A private choice")]).replace(
        "---\ntopic: gamma", "---\nvisibility: private\ntopic: gamma", 1
    )
    put(vault, "decisions/gamma.md", private, SOURCE_MTIME)
    # stray body content outside Current/History
    put(vault, "projects/delta.md", "# delta\n", OWNER_MTIME)
    stray = (
        decision_file("delta", [line("A choice next to stray text")])
        + f"\n## Call notes\n{STRAY_TEXT}\n"
    )
    put(vault, "decisions/delta.md", stray, SOURCE_MTIME)
