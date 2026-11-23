from collections.abc import Mapping, Sequence

from juno.core import unreadable

Check = tuple[str, str, str]
OPENING_LEFT_MIN = 1500
DAY_SECONDS = 86400


def rules_size_checks(
    core_size: int,
    global_size: int,
    opening_size: int,
    core_cap: int,
    global_cap: int,
    hook_cap: int,
) -> list[Check]:
    """core.md, global rules and the whole rules block against their caps.

    A file near its own cap is `budget_checks`' warning; this line warns when the rules
    block leaves under OPENING_LEFT_MIN chars of the hook cap for packs and the newest card.
    """
    left = max(hook_cap - opening_size, 0)
    detail = (
        f"core {core_size}/{core_cap}, global {global_size}/{global_cap}, "
        f"opening {opening_size}/{hook_cap} ({left} left for packs and the newest card)"
    )
    if left < OPENING_LEFT_MIN:
        return [("WARN", "rules size", f"{detail}; under {OPENING_LEFT_MIN} left")]
    return [("PASS", "rules size", detail)]


def snapshot_checks(
    repo: bool, last: tuple[int, str] | None, remotes: Sequence[str], now: float
) -> list[Check]:
    if not repo:
        return [("INFO", "snapshot", "the vault is not a git repository of its own, or git fails")]
    if last is None:
        return [("INFO", "snapshot", "no snapshot commit yet; run juno snapshot")]
    stamp, sha = last
    days = max(int(now - stamp), 0) // DAY_SECONDS
    age = "today" if days == 0 else f"{days} day{'s' if days != 1 else ''} ago"
    where = (
        f"remote {', '.join(remotes)} is configured, which does not prove the backup is current"
        if remotes
        else "no remote configured"
    )
    return [("INFO", "snapshot", f"last snapshot commit {age} ({sha[:7]}); {where}")]


def unreadable_checks(paths: Sequence[str]) -> list[Check]:
    if not paths:
        return []
    return [("WARN", "unreadable", f"{unreadable.describe(paths)}; their checks above ignore them")]


def stale_path_checks(
    recorded: Mapping[str, Sequence[str]], missing: Sequence[str], vault: str
) -> list[Check]:
    """Juno-written settings entries of another vault, while the entries for this one are missing.

    `recorded` holds the entries of the install manifest that are still in the settings and do
    not match the vault from the config; `missing` the entries the config's vault needs that the
    settings lack. `juno init` adds the missing ones (it leaves the old ones in place).
    """
    old = [e for entries in recorded.values() for e in entries]
    if not old or not missing:
        return []
    shown = ", ".join(old[:3]) + (f" (and {len(old) - 3} more)" if len(old) > 3 else "")
    return [
        (
            "WARN",
            "settings paths",
            f"settings.json holds Juno entries for another vault ({shown}) and lacks "
            f"{len(missing)} for {vault}; run juno init",
        )
    ]
