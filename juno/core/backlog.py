import re
import time
from collections.abc import Callable

from juno.core import fold, privacy
from juno.core.detect import Tickets
from juno.core.errors import Refused
from juno.core.redact import find_secrets
from juno.core.vaultfmt import Fields, parse_frontmatter

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,59}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SOURCE_RE = re.compile(r"^https?://[^\s\"'<>\[\]]{3,500}$")
TITLE_START_RE = re.compile(r"^[^\W_]", re.U)
HEADING_RE = re.compile(r"^#{1,6}\s")
STATUSES = ("inbox", "next", "doing", "waiting", "done", "dropped")
CLOSED = ("done", "dropped")
PRIORITIES = ("p1", "p2", "p3")
SUGGEST_STATUSES = ("done", "waiting", "doing")
SECTIONS = (("Task", 1), ("Why", 3), ("Research", 8), ("First step", 1))
SECTION_KEYS = {"task": "Task", "why": "Why", "research": "Research", "first-step": "First step"}
TITLE_CHARS = (5, 120)
LOG_CHARS = (10, 200)
REASON_CHARS = 120
BODY_BYTES = 8000
APPEND_CHARS = 1500
SECTION_CHARS = 4000
NOTE_LINE = "- Backlog: [[backlog/%s]] (tracked as a personal item)"

Row = dict[str, object]


def check_private(text: str, label: str) -> str:
    return privacy.check(text, label)


def checked_line(raw: str, label: str, low: int, high: int) -> str:
    value = privacy.clean_line(raw, label)
    if not low <= len(value) <= high:
        raise Refused(f"{label} must be {low}-{high} characters")
    return value


def check_slug(slug: str | None) -> str:
    if not SLUG_RE.match(slug or ""):
        raise Refused(
            f"{slug!r} is not a backlog slug (^[a-z0-9][a-z0-9-]{{2,59}}$; see `juno backlog list`)"
        )
    return str(slug)


def check_title(raw: str) -> str:
    title = checked_line(raw, "title", *TITLE_CHARS)
    if not TITLE_START_RE.match(title) or " #" in title or "[[" in title:
        raise Refused("title must start with a letter or digit and hold no ' #' or wikilink")
    return title


def check_choice(value: str, choices: tuple[str, ...], label: str) -> str:
    if value not in choices:
        raise Refused(f"{label} must be one of {', '.join(choices)}")
    return value


def check_date(value: str | None, label: str = "due") -> str:
    try:
        if DATE_RE.match(value or ""):
            time.strptime(str(value), "%Y-%m-%d")
            return str(value)
    except ValueError:
        pass
    raise Refused(f"{label} must be a date YYYY-MM-DD")


def check_tickets(raw: list[str] | None, tickets: Tickets) -> list[str]:
    keys: list[str] = []
    for value in raw or []:
        if tickets.pattern is None:
            raise Refused("ticket keys need [jira] prefixes in the config")
        if not tickets.pattern.fullmatch(value or ""):
            raise Refused(f"{value!r} is not a ticket key")
        if value.upper() not in keys:
            keys.append(value.upper())
    return keys


def check_repos(raw: list[str] | None) -> list[str]:
    repos: list[str] = []
    for value in raw or []:
        if not REPO_RE.match(value or ""):
            raise Refused(f"{value!r} is not a repo name")
        if value not in repos:
            repos.append(value)
    return repos


def check_source(raw: str | None) -> str:
    if raw is None:
        return "prompt"
    if not SOURCE_RE.match(raw) or find_secrets(raw):
        raise Refused("--source must be one http(s) URL without spaces, quotes or secrets")
    return raw


def parse_body(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in fold.nfc(text).replace("\r\n", "\n").split("\n"):
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current = heading.group(1)
            if current not in dict(SECTIONS) or current in sections:
                raise Refused(
                    "body headings must be exactly ## Task, ## Why, ## Research, ## First step"
                )
            sections[current] = []
        elif HEADING_RE.match(line) or line.strip() == "---":
            raise Refused("body holds a heading or rule line other than the four sections")
        elif line.strip():
            if current is None:
                raise Refused("body text before ## Task")
            sections[current].append(line.rstrip())
    if list(sections) != [name for name, _ in SECTIONS]:
        raise Refused(
            "body needs the sections ## Task, ## Why, ## Research, ## First step in this order"
        )
    for name, most in SECTIONS:
        if not 1 <= len(sections[name]) <= most:
            raise Refused(f"## {name} needs 1-{most} non-empty lines")
    check_private("\n".join(x for lines in sections.values() for x in lines), "body")
    return sections


def parse_addition(text: str) -> list[str]:
    lines = [x.rstrip() for x in fold.nfc(text).replace("\r\n", "\n").split("\n") if x.strip()]
    if not lines:
        raise Refused("--body-file holds no text to add")
    if any(HEADING_RE.match(x) or x.strip() == "---" for x in lines):
        raise Refused("--body-file holds a heading or rule line; give only the lines to add")
    if len("\n".join(lines)) > APPEND_CHARS:
        raise Refused(f"the text to add is over {APPEND_CHARS} characters")
    check_private("\n".join(lines), "body")
    return lines


def yaml_list(values: list[str]) -> str:
    return "[" + ", ".join(values) + "]"


def render_item(fields: dict[str, object], sections: dict[str, list[str]], log_line: str) -> str:
    head = [
        "---",
        f"title: {fields['title']}",
        "status: inbox",
        f"priority: {fields['priority']}",
    ]
    if fields.get("due"):
        head.append(f"due: {fields['due']}")
    head += [
        f"added: {fields['added']}",
        f"updated: {fields['added']}",
        f"source: {fields['source']}",
    ]
    for name in ("tickets", "repos"):
        values = fields.get(name)
        if isinstance(values, list) and values:
            head.append(f"{name}: {yaml_list([str(v) for v in values])}")
    head += [f"src: {fields['src']}", "---"]
    body: list[str] = []
    for name, _ in SECTIONS:
        body += ["## " + name, *sections[name]]
    return "\n".join([*head, *body, "## Log", log_line]) + "\n"


def as_text(value: object) -> str:
    return " ".join(str(v) for v in value) if isinstance(value, list) else str(value or "")


def as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return [str(value)] if value else []


def item_problem(fields: Fields) -> str | None:
    if not as_text(fields.get("title")).strip():
        return "no title"
    if as_text(fields.get("status")) not in STATUSES:
        return "bad status"
    if as_text(fields.get("priority")) not in PRIORITIES:
        return "bad priority"
    dated = ["added", "updated"] + (["due"] if fields.get("due") else [])
    for name in dated:
        if not DATE_RE.match(as_text(fields.get(name))):
            return "bad " + name
    return None


def item_row(slug: str, text: str, safe: bool = True) -> Row:
    fields, _ = parse_frontmatter(text)
    problem = (
        "unsafe path"
        if not safe
        else ("bad slug" if not SLUG_RE.match(slug) else item_problem(fields))
    )
    return {
        "slug": slug,
        "title": as_text(fields.get("title")),
        "status": as_text(fields.get("status")),
        "priority": as_text(fields.get("priority")),
        "due": as_text(fields.get("due")),
        "added": as_text(fields.get("added")),
        "updated": as_text(fields.get("updated")),
        "tickets": as_list(fields.get("tickets")),
        "repos": as_list(fields.get("repos")),
        "problem": problem,
    }


def order_key(row: Row) -> tuple[bool, str, str, str, str]:
    return (
        row["status"] in CLOSED,
        str(row["priority"]),
        str(row["due"]) or "9999-99-99",
        str(row["added"]),
        str(row["slug"]),
    )


def new_slug(title: str, taken: Callable[[str], bool], preset: str) -> str:
    base = fold.slug(title, preset)
    if not SLUG_RE.match(base):
        raise Refused("the title gives no usable slug (needs 3+ letters or digits)")
    slug, number = base, 2
    while taken(slug):
        suffix = f"-{number}"
        slug, number = base[: 60 - len(suffix)].rstrip("-") + suffix, number + 1
    return slug


def head_span(text: str) -> int:
    match = re.match(r"^---[ \t]*\n.*?\n---[ \t]*(?:\n|$)", text, re.S)
    if not match:
        raise Refused("the item has no frontmatter; fix it by hand")
    return match.end()


def set_field(text: str, name: str, value: str | None) -> str:
    end = head_span(text)
    head = text[:end]
    pattern = re.compile(rf"(?m)^{re.escape(name)}:.*\n")
    if value is None:
        head = pattern.sub("", head, count=1)
    elif pattern.search(head):
        head = pattern.sub(lambda _: f"{name}: {value}\n", head, count=1)
    else:
        close = head.rstrip("\n").rfind("\n")
        head = head[: close + 1] + f"{name}: {value}\n" + head[close + 1 :]
    return head + text[end:]


def append_line(text: str, heading: str, line: str) -> str:
    head = re.search(rf"^## {re.escape(heading)}[ \t]*$\n?", text, re.M)
    if not head:
        return text.rstrip("\n") + f"\n\n## {heading}\n{line}\n"
    nxt = re.search(r"^## ", text[head.end() :], re.M)
    end = head.end() + nxt.start() if nxt else len(text)
    block = text[head.end() : end].rstrip("\n")
    tail = "\n\n" if nxt else "\n"
    return (
        text[: head.end()] + (block + "\n" if block else "") + line + tail + text[end:].lstrip("\n")
    )


def log_entry(date: str, text: str) -> str:
    return f"- {date} · {text}"


def field(text: str, name: str) -> str:
    fields, _ = parse_frontmatter(text)
    return as_text(fields.get(name))


def status_change(text: str, status: str | None, reason: str | None) -> tuple[str, list[str]]:
    current = field(text, "status")
    if status is None or status == current:
        return text, []
    note = f"status {current or '-'} -> {status}" + (f" ({reason})" if reason else "")
    return set_field(text, "status", status), [note]


def priority_change(text: str, priority: str | None) -> tuple[str, list[str]]:
    current = field(text, "priority")
    if priority is None or priority == current:
        return text, []
    return set_field(text, "priority", priority), [f"priority {current or '-'} -> {priority}"]


def due_change(text: str, due: str | None) -> tuple[str, list[str]]:
    current = field(text, "due")
    if due is None or due == current or (due == "none" and not current):
        return text, []
    if due == "none":
        return set_field(text, "due", None), ["due removed"]
    return set_field(text, "due", due), [f"due {due}"]


def list_change(
    text: str, name: str, label: str, add: list[str], remove: list[str]
) -> tuple[str, list[str]]:
    fields, _ = parse_frontmatter(text)
    current = as_list(fields.get(name))
    new = [v for v in add if v not in current]
    gone = [v for v in remove if v in current]
    if not new and not gone:
        return text, []
    values = [v for v in current if v not in gone] + new
    notes = [f"{label} {v} linked" for v in new] + [f"{label} {v} unlinked" for v in gone]
    return set_field(text, name, yaml_list(values) if values else None), notes


def apply_changes(
    text: str,
    date: str,
    status: str | None = None,
    priority: str | None = None,
    due: str | None = None,
    tickets: list[str] | None = None,
    reason: str | None = None,
    untickets: list[str] | None = None,
    repos: list[str] | None = None,
    unrepos: list[str] | None = None,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    steps: list[Callable[[str], tuple[str, list[str]]]] = [
        lambda t: status_change(t, status, reason),
        lambda t: priority_change(t, priority),
        lambda t: due_change(t, due),
        lambda t: list_change(t, "tickets", "ticket", tickets or [], untickets or []),
        lambda t: list_change(t, "repos", "repo", repos or [], unrepos or []),
    ]
    for step in steps:
        text, made = step(text)
        notes += made
    if not notes:
        return text, notes
    text = set_field(text, "updated", date)
    for note in notes:
        text = append_line(text, "Log", log_entry(date, note))
    return text, notes


def section_span(text: str, heading: str) -> tuple[int, int]:
    head = re.search(rf"^## {re.escape(heading)}[ \t]*$\n?", text, re.M)
    if not head:
        raise Refused(f"the item has no ## {heading} section; fix it by hand")
    nxt = re.search(r"^## ", text[head.end() :], re.M)
    return head.end(), head.end() + nxt.start() if nxt else len(text)


def append_section(text: str, heading: str, lines: list[str]) -> str:
    start, end = section_span(text, heading)
    if len(text[start:end].strip()) + len("\n".join(lines)) + 1 > SECTION_CHARS:
        raise Refused(
            f"## {heading} would grow over {SECTION_CHARS} characters; shorten the text or "
            "start a new item"
        )
    body = text[start:end].rstrip("\n")
    rest = text[start + len(body) : end] or "\n"
    return text[:start] + (body + "\n" if body else "") + "\n".join(lines) + rest + text[end:]


def numbered(text: str) -> str:
    out: list[str] = []
    number = 0
    section: str | None = None
    for line in text.split("\n"):
        heading = re.match(r"^## (.+?)[ \t]*$", line)
        if heading:
            section, number = heading.group(1), 0
            out.append(line)
        elif section in dict(SECTIONS) and line.strip():
            number += 1
            out.append(f"{number}: {line}")
        else:
            out.append(line)
    return "\n".join(out)


def edit_section(text: str, heading: str, number: int, new_lines: list[str]) -> str:
    start, end = section_span(text, heading)
    lines = text[start:end].split("\n")
    filled = [i for i, line in enumerate(lines) if line.strip()]
    if not 1 <= number <= len(filled):
        raise Refused(
            f"## {heading} has {len(filled)} line(s); see `juno backlog show <slug> --numbered`"
        )
    if not new_lines and len(filled) == 1:
        raise Refused(f"## {heading} would be empty; replace the line instead")
    index = filled[number - 1]
    lines[index : index + 1] = new_lines
    block = "\n".join(lines)
    if len(block.strip()) > SECTION_CHARS:
        raise Refused(f"## {heading} would grow over {SECTION_CHARS} characters")
    return text[:start] + block + text[end:]
