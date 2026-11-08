import datetime
import plistlib

LABEL = "dev.juno.stale-weekly"
UNIT = "juno-stale-weekly"
WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def weekday_index(name: str) -> int | None:
    key = name.strip().lower()[:3]
    return WEEKDAYS.index(key) if key in WEEKDAYS else None


def window_problem(weekday: str, start: int, end: int) -> str | None:
    if weekday_index(weekday) is None:
        return f"[stale] weekday {weekday!r} is not one of {', '.join(WEEKDAYS)}"
    if not 0 <= start <= end <= 23:
        return "[stale] start_hour and end_hour must satisfy 0 <= start_hour <= end_hour <= 23"
    return None


def command(python: str, plugin_root: str) -> list[str]:
    return [python, "-I", "-S", f"{plugin_root}/juno/cli.py", "stale", "--fix", "--job"]


def plist(
    argv: list[str], env: dict[str, str], log: str, weekday: str, start: int, end: int
) -> bytes:
    day = weekday_index(weekday)
    slots = [{"Weekday": day, "Hour": hour, "Minute": 0} for hour in range(start, end + 1)]
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": argv,
            "EnvironmentVariables": env,
            "StartCalendarInterval": slots,
            "StandardOutPath": log,
            "StandardErrorPath": log,
            "RunAtLoad": False,
        }
    )


def quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def service(argv: list[str], env: dict[str, str], log: str) -> str:
    lines = [
        "[Unit]",
        "Description=Juno weekly stale memory fixer",
        "",
        "[Service]",
        "Type=oneshot",
    ]
    lines += [f"Environment={quote(f'{k}={v}')}" for k, v in sorted(env.items())]
    lines += [
        f"ExecStart={' '.join(quote(a) for a in argv)}",
        f"StandardOutput=append:{log}",
        f"StandardError=append:{log}",
    ]
    return "\n".join(lines) + "\n"


def timer(weekday: str, start: int, end: int) -> str:
    day = WEEKDAYS[weekday_index(weekday) or 0].capitalize()
    return (
        "[Unit]\nDescription=Juno weekly stale memory fixer\n\n[Timer]\n"
        f"OnCalendar={day} *-*-* {start:02d}..{end:02d}:00:00\nPersistent=true\n\n"
        "[Install]\nWantedBy=timers.target\n"
    )


def shell_word(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def cron_line(argv: list[str], weekday: str, start: int, end: int, log: str) -> str:
    day = weekday_index(weekday) or 0
    cmd = " ".join(shell_word(a) for a in argv)
    return f"0 {start}-{end} * * {day} {cmd} >> {shell_word(log)} 2>&1"


def next_fire(now: datetime.datetime, weekday: str, start: int, end: int) -> datetime.datetime:
    python_day = ((weekday_index(weekday) or 0) - 1) % 7
    for offset in range(8):
        date = now.date() + datetime.timedelta(days=offset)
        if date.weekday() != python_day:
            continue
        for hour in range(start, end + 1):
            slot = datetime.datetime.combine(date, datetime.time(hour), now.tzinfo)
            if slot > now:
                return slot
    return now
