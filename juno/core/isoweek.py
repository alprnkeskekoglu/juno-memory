import datetime
import re

from juno.core.errors import Refused

WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


def week_label(day: datetime.date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def last_week(now: float) -> str:
    return week_label(datetime.date.fromtimestamp(now) - datetime.timedelta(days=7))


def bounds(label: str) -> tuple[float, float]:
    found = WEEK_RE.match(label)
    if not found:
        raise Refused(f"{label!r} is not an ISO week (YYYY-Www)")
    try:
        monday = datetime.date.fromisocalendar(int(found[1]), int(found[2]), 1)
    except ValueError:
        raise Refused(f"{label!r} is not an ISO week (YYYY-Www)") from None

    def at(day: datetime.date) -> float:
        return datetime.datetime.combine(day, datetime.time()).timestamp()

    return at(monday), at(monday + datetime.timedelta(days=7))


def previous(label: str) -> str:
    monday = datetime.date.fromtimestamp(bounds(label)[0])
    return week_label(monday - datetime.timedelta(days=7))
