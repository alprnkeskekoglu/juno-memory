import re
import unicodedata
from dataclasses import dataclass

TOP_KINDS = {
    "protocol": "protocol",
    "rules": "rule",
    "self": "self",
    "decisions": "decision",
    "knowledge": "knowledge",
    "procedures": "procedure",
    "projects": "project",
    "tasks": "task",
    "handoffs": "handoff",
    "notes": "note",
    "backlog": "backlog",
    "ideas": "idea",
    "archive": "archive",
    "_generated": "generated",
}
ROOT_KINDS = {
    "core.md": "core",
    "juno-brain.toml": "config",
    "inbox.md": "inbox",
    "threads.md": "threads",
    "pitfalls.md": "pitfalls",
}
PROTECTED_TOPS = frozenset({"rules", "tasks", "handoffs", "projects", "decisions", "self"})
NEVER_INJECTED_KINDS = frozenset({"archive", "generated", "config"})

WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")
FM_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:(?:[ \t]+(.*)|[ \t]*)$")
RULE_LINE_RE = re.compile(
    r'^- (\d{4}-\d{2}-\d{2}) · (.+?) · src: (?:user (\S{1,8}) · "(.*)"|unquoted)\s*$'
)
CARD_HEAD_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) · (.+?) · (.+?) · (\S{1,8})[ \t]*$")
THREAD_RE = re.compile(r"^- \[([^\]\n]+)\] (.+)$")
FINISHED_STATUSES = ("done", "dropped", "archived")

Fields = dict[str, str | list[str]]


def kind_of(relpath: str) -> str:
    normal = unicodedata.normalize("NFC", relpath).replace("\\", "/").lstrip("/")
    if "/" not in normal:
        return ROOT_KINDS.get(normal, "other")
    return TOP_KINDS.get(normal.split("/", 1)[0].casefold(), "other")


def top_of(relpath: str) -> str:
    return unicodedata.normalize("NFC", relpath).split("/", 1)[0].casefold()


def rule_scope(relpath: str) -> str | None:
    if kind_of(relpath) != "rule" or not relpath.endswith(".md"):
        return None
    parts = relpath.split("/")
    if len(parts) == 3 and parts[1] in ("repos", "topics"):
        kind = "repo" if parts[1] == "repos" else "topic"
        return f"{kind}:{parts[2][:-3]}"
    if len(parts) != 2:
        return None
    name = parts[1][:-3]
    return "global" if name == "global" else "ticket:" + name


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def strip_comment(value: str) -> str:
    value = value.strip()
    if value.startswith(('"', "'")):
        quote = value[0]
        end = value.find(quote, 1)
        return value[: end + 1] if end > 0 else value
    return re.split(r"[ \t]+#", value, maxsplit=1)[0].strip()


def parse_frontmatter(text: str) -> tuple[Fields, str]:
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return {}, text
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() in ("---", "...")), None)
    if end is None:
        return {}, text
    body = "\n".join(lines[end + 1 :])
    return parse_fields(lines[1:end]), body


def block_list(lines: list[str], start: int) -> tuple[list[str], bool, int]:
    items: list[str] = []
    bad = False
    j = start
    while j < len(lines):
        nxt = lines[j].rstrip("\r")
        stripped = nxt.strip()
        if not stripped or stripped.startswith("#"):
            j += 1
            continue
        if bad:
            if nxt[0] in " \t":
                j += 1
                continue
            break
        if stripped.startswith("- ") or stripped == "-":
            items.append(unquote(strip_comment(stripped[1:])))
            j += 1
        elif nxt[0] in " \t":
            bad = True
            j += 1
        else:
            break
    return items, bad, j


def parse_fields(lines: list[str]) -> Fields:
    fields: Fields = {}
    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\r")
        i += 1
        if not line.strip() or line.lstrip().startswith("#") or line[0] in " \t":
            continue
        match = FM_KEY_RE.match(line)
        if not match:
            continue
        key = match.group(1).lower()
        raw = strip_comment(match.group(2) or "")
        if raw in ("|", ">", "|-", ">-", "|+", ">+") or raw[:1] in ("{", "&", "*", "!"):
            while i < len(lines) and (not lines[i].strip() or lines[i][0] in " \t"):
                i += 1
            continue
        if raw == "":
            items, bad, i = block_list(lines, i)
            if items and not bad:
                fields[key] = items
            continue
        links = WIKILINK_RE.findall(raw)
        if links:
            fields[key] = [f"[[{x}]]" for x in links]
        elif raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1].strip()
            fields[key] = [unquote(p) for p in inner.split(",") if p.strip()] if inner else []
        else:
            fields[key] = unquote(raw)
    return fields


def field_str(fields: Fields, name: str) -> str:
    value = fields.get(name)
    return value.strip().casefold() if isinstance(value, str) else ""


def field_items(fields: Fields, name: str) -> list[str]:
    value = fields.get(name)
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in value.split(",") if p.strip()] if isinstance(value, str) else []


def is_private(fields: Fields) -> bool:
    return field_str(fields, "visibility") == "private"


def is_archived(fields: Fields) -> bool:
    return field_str(fields, "status") == "archived"


def hidden_reason(relpath: str, text: str, retired: set[str] | None) -> str | None:
    kind = kind_of(relpath)
    if kind in NEVER_INJECTED_KINDS:
        return kind
    if retired and relpath in retired:
        return "retired"
    fields, _ = parse_frontmatter(text)
    if is_archived(fields):
        return "archived"
    if is_private(fields):
        return "private"
    return None


def section(text: str, name: str) -> str:
    match = re.search(
        r"^##\s+" + re.escape(name) + r"\s*$(.*?)(?=^#{1,2}\s|\Z)", text, re.MULTILINE | re.DOTALL
    )
    return match.group(1).strip() if match else ""


@dataclass(frozen=True)
class RuleEntry:
    number: int
    line: str
    date: str | None
    text: str | None
    pid: str | None
    quote: str | None


def rule_entries(text: str) -> list[RuleEntry]:
    found: list[RuleEntry] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.startswith("- "):
            continue
        match = RULE_LINE_RE.match(line)
        if match:
            found.append(
                RuleEntry(number, line, match[1], match[2], match[3] or None, match[4] or None)
            )
        else:
            found.append(RuleEntry(number, line, None, None, None, None))
    return found


def rule_texts(text: str) -> list[str]:
    return [e.text for e in rule_entries(text) if e.text]


def injected_rules(text: str) -> str:
    return "\n".join("- " + t for t in rule_texts(text))


def card_field(body: str, name: str) -> str | None:
    match = re.search(r"^" + name + r":[ \t]*(.*)$", body, re.MULTILINE)
    return match.group(1).strip() if match else None


def items(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip() and x.strip() != "-"]


def parse_cards(text: str, relpath: str) -> list[dict[str, object]]:
    heads = list(re.finditer(r"^#{1,2} .*$", text, re.MULTILINE))
    cards: list[dict[str, object]] = []
    for i, head in enumerate(heads):
        line = head.group(0)
        if not line.startswith("## "):
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[head.start() : end].strip()
        match = CARD_HEAD_RE.match(line)
        card: dict[str, object] = {
            "path": relpath,
            "head": line,
            "body": body,
            "valid": bool(match),
        }
        if match:
            card.update(
                time=match.group(1),
                key=match.group(2),
                label=match.group(3),
                seg=match.group(4),
                repos=items(card_field(body, "Repos")),
                tickets=items(card_field(body, "Tickets")),
                learned=card_field(body, "Learned"),
                auto=re.search(r"^auto:\s*true\s*$", body, re.MULTILINE) is not None,
            )
        cards.append(card)
    return cards


def card_mentions(card: dict[str, object], kind: str, key: str) -> bool:
    tickets = card.get("tickets")
    repos = card.get("repos")
    if kind == "ticket":
        listed = [str(t).upper() for t in tickets] if isinstance(tickets, list) else []
        return str(card.get("key", "")).upper() == key or key in listed
    return isinstance(repos, list) and key in repos


def thread_lines(text: str, key: str, budget: int, limit: int) -> str:
    picked: list[str] = []
    for line in text.splitlines():
        match = THREAD_RE.match(line.rstrip())
        if not match or len(picked) >= limit:
            continue
        tags = [t.strip().casefold() for t in match.group(1).split(",")]
        if key.casefold() in tags and sum(len(p) + 1 for p in picked) + len(line) <= budget:
            picked.append(line.rstrip())
    return "\n".join(picked)


def decision_current(text: str) -> str:
    _, body = parse_frontmatter(text)
    return section(body, "Current")
