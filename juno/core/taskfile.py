import re
from collections.abc import Sequence

from juno.core import privacy, vaultfmt

TITLE_MAX = 200
STATUS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
URL_RE = re.compile(r"^https?://[^\s\"']+$")
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
PLAIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/()+,-]*$")
ORDER = ("jira", "title", "status", "url", "repos", "slack_checked", "created", "updated")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
QUOTED = ("title",)
NEW_BODY = "## Decisions\n\n## Notes"

Updates = dict[str, str | list[str]]


def clean_title(raw: str, prefixes: Sequence[str] | None = None) -> tuple[str, str | None]:
    title = " ".join(raw.replace('"', "'").split())[:TITLE_MAX]
    if not title:
        return "", "title is empty"
    problem = privacy.scan(title, prefixes)
    if problem:
        return "", f"title looks like it holds private data ({problem})"
    return title, None


def clean_status(raw: str) -> tuple[str, str | None]:
    status = "-".join(raw.strip().lower().split())
    if not STATUS_RE.match(status):
        return "", f"status {raw!r} is not a plain word (letters, digits, - . _; at most 40)"
    return status, None


def clean_url(raw: str, prefixes: Sequence[str] | None = None) -> tuple[str, str | None]:
    url = raw.strip()
    if not URL_RE.match(url) or privacy.scan(url, prefixes):
        return "", f"url {raw!r} is not a plain http(s) link"
    return url, None


def clean_day(raw: str) -> tuple[str, str | None]:
    day = raw.strip()
    return (day, None) if DAY_RE.match(day) else ("", f"date {raw!r} is not YYYY-MM-DD")


def clean_repos(raw: list[str]) -> tuple[list[str], str | None]:
    bad = [r for r in raw if not REPO_RE.match(r)]
    return ([] if bad else list(dict.fromkeys(raw))), (
        f"repo {bad[0]!r} is not a repo name" if bad else None
    )


def default_url(base_url: str, key: str) -> str:
    base = base_url.strip().rstrip("/")
    return f"{base}/browse/{key}" if URL_RE.match(base) else ""


def render_value(name: str, value: str | list[str]) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(value) + "]"
    if name in QUOTED and not PLAIN_RE.match(value):
        return f'"{value}"'
    return value


def field_line(name: str, value: str | list[str]) -> str:
    return f"{name}: {render_value(name, value)}"


def frontmatter_end(lines: list[str]) -> int | None:
    if not lines or lines[0].rstrip() != "---":
        return None
    return next((i for i in range(1, len(lines)) if lines[i].rstrip() in ("---", "...")), None)


def merge(existing: str, key: str, updates: Updates, today: str) -> str:
    values: Updates = {**updates, "updated": today}
    lines = existing.split("\n")
    end = frontmatter_end(lines)
    if end is None:
        values = {"jira": key, "created": today, **values}
        head = [field_line(n, values[n]) for n in ORDER if n in values]
        body = existing.strip("\n") or NEW_BODY
        return "\n".join(["---", *head, "---", "", body]) + "\n"
    front = lines[1:end]
    present: set[str] = set()
    for index, line in enumerate(front):
        match = vaultfmt.FM_KEY_RE.match(line)
        name = match.group(1).lower() if match else ""
        if name in values and name not in present and not line[:1].isspace():
            front[index] = field_line(name, values[name])
            present.add(name)
    missing = [n for n in ORDER if n in values and n not in present]
    if not any(re.match(r"jira[ \t]*:", line, re.I) for line in front) and "jira" not in missing:
        missing.insert(0, "jira")
        values["jira"] = key
    front += [field_line(n, values[n]) for n in missing]
    return "\n".join([lines[0], *front, *lines[end:]])


def row(name: str, text: str) -> dict[str, object]:
    fields, _ = vaultfmt.parse_frontmatter(text)
    return {
        "key": name,
        "status": vaultfmt.field_str(fields, "status"),
        "title": str(fields.get("title") or ""),
        "url": str(fields.get("url") or ""),
        "repos": vaultfmt.field_items(fields, "repos") or vaultfmt.field_items(fields, "repo"),
    }
