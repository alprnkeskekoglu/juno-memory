import hashlib
import re
from collections.abc import Callable
from typing import Any

from juno.core import fold, vaultfmt
from juno.core.inject import CONTEXT_LABEL, RULES_LABEL, neutralize
from juno.core.view import VaultView

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
KNOWLEDGE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]{0,99})\.md$")
TRIGGERS_RE = re.compile(r"^triggers:[ \t]*(.*)$", re.I)
WHO_RE = re.compile(r"[^A-Za-z0-9_-]")
PITFALLS_FILE = "pitfalls.md"
PROCEDURE_DIR = "procedures"
PROCEDURE_PREFIX = "procedure-"
PROCEDURE_BLOCK_CHARS = 600
STEP_RE = re.compile(r"^\d+\. ")
TOPIC_DIR = "topics"
REPO_DIR = "repos"
MAX_TRIGGERS = 20
TRIGGER_CHARS = (2, 60)
SCAN_CHARS = 20000
SUMMARY_LINES = 2
SUMMARY_CHARS = 240
PATH_KEYS = ("file_path", "path", "notebook_path")
PATTERN_KEYS = {"Glob": "pattern", "Grep": "glob"}
PITFALL_LINE_CHARS = 800
GENERAL_LINES = 5
GENERAL_CHARS = 1200

Entry = list[Any]
_patterns: dict[str, re.Pattern[str]] = {}


def topic_slug(name: str) -> str | None:
    """Slug of a rules-relative name like ``topics/git.md``."""
    if not (name.startswith(TOPIC_DIR + "/") and name.endswith(".md")):
        return None
    slug = name[len(TOPIC_DIR) + 1 : -len(".md")]
    return slug if SLUG_RE.match(slug) else None


def topic_relpath(slug: str) -> str:
    return f"rules/{TOPIC_DIR}/{slug}.md"


def split_triggers(raw: str | None, preset: str = "none") -> list[str]:
    found: list[str] = []
    for part in (raw or "").split(","):
        trigger = fold.fold(part, preset)
        if trigger and trigger not in found:
            found.append(trigger)
    return found


def trigger_problem(trigger: str) -> str | None:
    low, high = TRIGGER_CHARS
    if not low <= len(trigger) <= high:
        return f"trigger {trigger!r} must be {low} to {high} characters"
    if not any(ch.isalnum() for ch in trigger):
        return f"trigger {trigger!r} needs a letter or digit"
    if "·" in trigger or any(not ch.isprintable() for ch in trigger):
        return f"trigger {trigger!r} has a '·' or a control character"
    return None


def file_triggers(text: str, preset: str = "none") -> list[str]:
    for line in text.splitlines():
        if line.startswith("- "):
            break
        match = TRIGGERS_RE.match(line.strip())
        if match:
            return split_triggers(match.group(1), preset)
    return []


def set_triggers(text: str, scope: str, triggers: list[str]) -> str:
    line = "triggers: " + ", ".join(triggers)
    lines = text.splitlines() if text else [f"# Rules: {scope}", ""]
    for i, current in enumerate(lines):
        if current.startswith("- "):
            break
        if TRIGGERS_RE.match(current.strip()):
            lines[i] = line
            return "\n".join(lines) + "\n"
    at = 1 if lines and lines[0].startswith("#") else 0
    lines.insert(at, line)
    if len(lines) == at + 1 or lines[at + 1].strip():
        lines.insert(at + 1, "")
    return "\n".join(lines) + "\n"


def field_list(value: str | list[str] | None, preset: str) -> list[str]:
    if isinstance(value, list):
        return split_triggers(",".join(str(v) for v in value), preset)
    return split_triggers(value if isinstance(value, str) else "", preset)


def pitfall_line(line: str, preset: str = "none") -> dict[str, Any] | None:
    parts = line[2:].rstrip().split(" · ")
    if len(parts) != 4 or not parts[1].startswith("triggers:") or not parts[0].strip():
        return None
    raw = parts[1][len("triggers:") :].strip()
    triggers = [] if raw == "-" else split_triggers(raw, preset)
    if raw != "-" and (not triggers or any(trigger_problem(t) for t in triggers)):
        return None
    return {"text": parts[0].strip(), "triggers": triggers[:MAX_TRIGGERS], "date": parts[3].strip()}


def pitfall_lines(text: str, preset: str = "none") -> list[dict[str, Any]]:
    found = [
        pitfall_line(line, preset)
        for line in text.splitlines()
        if line.startswith("- ") and len(line) <= PITFALL_LINE_CHARS
    ]
    return [f for f in found if f]


def pitfall_name(text: str, preset: str = "none") -> str:
    digest = hashlib.sha256(fold.fold(text, preset).encode("utf-8")).hexdigest()[:8]
    return "pitfall-" + digest


def build_entries(files: list[tuple[str, str]], preset: str = "none") -> list[Entry]:
    entries: list[Entry] = []
    for relpath, text in files:
        if relpath == PITFALLS_FILE:
            for found in pitfall_lines(text, preset):
                if found["triggers"]:
                    entries.append(
                        [
                            pitfall_name(found["text"], preset),
                            relpath,
                            found["triggers"],
                            found["text"],
                        ]
                    )
            continue
        sub, name = relpath.split("/", 1)
        if sub == PROCEDURE_DIR:
            fields, _ = vaultfmt.parse_frontmatter(text)
            if (
                vaultfmt.is_archived(fields)
                or vaultfmt.is_private(fields)
                or not name.endswith(".md")
            ):
                continue
            triggers = [
                t for t in field_list(fields.get("triggers"), preset) if not trigger_problem(t)
            ][:MAX_TRIGGERS]
            if triggers and SLUG_RE.match(name[:-3]):
                entries.append([PROCEDURE_PREFIX + name[:-3], relpath, triggers])
            continue
        if sub == "rules":
            slug = topic_slug(name)
            if slug:
                entries.append(
                    ["topic-" + slug, relpath, file_triggers(text, preset)[:MAX_TRIGGERS]]
                )
            continue
        fields, _ = vaultfmt.parse_frontmatter(text)
        if vaultfmt.is_archived(fields) or vaultfmt.is_private(fields):
            continue
        triggers = [
            t for t in field_list(fields.get("triggers"), preset) if not trigger_problem(t)
        ][:MAX_TRIGGERS]
        found_name = KNOWLEDGE_RE.match(name)
        if triggers and found_name:
            entries.append(["knowledge-" + found_name.group(1), relpath, triggers])
    return [e for e in entries if e[2]]


def pattern(trigger: str) -> re.Pattern[str]:
    compiled = _patterns.get(trigger)
    if compiled is None:
        left = r"(?<![^\W_])" if trigger[0].isalnum() else ""
        right = r"(?![^\W_])" if trigger[-1].isalnum() else ""
        compiled = _patterns[trigger] = re.compile(left + re.escape(trigger) + right)
    return compiled


def matches(entries: list[Entry], text: str, preset: str = "none") -> list[Entry]:
    if not entries or not text:
        return []
    haystack = fold.fold(text[:SCAN_CHARS], preset)
    return [
        entry
        for entry in entries
        if any(t in haystack and pattern(t).search(haystack) for t in entry[2])
    ]


def tool_text(tool: object, tool_in: dict[str, Any]) -> str:
    parts = [str(tool or "")]
    if tool == "Bash" and isinstance(tool_in.get("command"), str):
        parts.append(tool_in["command"])
    keys = PATH_KEYS + ((PATTERN_KEYS[str(tool)],) if tool in PATTERN_KEYS else ())
    parts += [tool_in[k] for k in keys if isinstance(tool_in.get(k), str)]
    return "\n".join(parts)


def who(agent: object) -> str:
    if not agent:
        return "main"
    return "a-" + WHO_RE.sub("", str(agent))[:64]


def topic_block(view: VaultView, name: str, relpath: str) -> str:
    texts = [] if view.hidden(relpath) else vaultfmt.rule_texts(view.read(relpath))
    if not texts:
        return ""
    head = f"[topic {name[len('topic-') :]} · {relpath}]"
    return "\n".join([head] + ["- " + neutralize(t) for t in texts])


def short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SUMMARY_CHARS else text[: SUMMARY_CHARS - 1].rstrip() + "…"


def knowledge_block(view: VaultView, relpath: str) -> str:
    text = view.read(relpath)
    if view.hidden(relpath, text):
        return ""
    fields, body = vaultfmt.parse_frontmatter(text)
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    title = str(fields.get("title") or "").strip()
    if not title:
        title = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), "")
    title = title or relpath.rsplit("/", 1)[-1][:-3]
    summary = fields.get("summary")
    if isinstance(summary, str) and summary.strip():
        picked = [summary]
    else:
        picked = [ln for ln in lines if not ln.startswith("#") and not ln.startswith("Links:")][
            :SUMMARY_LINES
        ]
    head = f"[Knowledge · {relpath}{view.stale_mark(relpath)}] {neutralize(short(title))}"
    return "\n".join([head] + [neutralize(short(p)) for p in picked])


def procedure_text(
    title: str, day: str, sid8: str, steps: list[str], triggers: list[str], repos: list[str]
) -> str:
    fields = {"title": title, "updated": day, "source": sid8}
    if triggers:
        fields["triggers"] = f"[{', '.join(triggers)}]"
    if repos:
        fields["repos"] = f"[{', '.join(repos)}]"
    head = "---\n" + "".join(f"{k}: {v}\n" for k, v in fields.items()) + "---\n"
    return head + "\n" + "\n".join(f"{n}. {s}" for n, s in enumerate(steps, 1)) + "\n"


def procedure_block(view: VaultView, relpath: str) -> str:
    text = view.read(relpath)
    if view.hidden(relpath, text):
        return ""
    fields, body = vaultfmt.parse_frontmatter(text)
    title = str(fields.get("title") or "").strip() or relpath.rsplit("/", 1)[-1][:-3]
    lines = [f"[Procedure · {relpath}] {neutralize(short(title))}"]
    used = len(lines[0])
    for line in body.splitlines():
        step = neutralize(line.strip())
        if not STEP_RE.match(step):
            continue
        if used + len(step) + 1 > PROCEDURE_BLOCK_CHARS:
            break
        lines.append(step)
        used += len(step) + 1
    return "\n".join(lines)


def pitfall_block(view: VaultView, text: str) -> str:
    if view.hidden(PITFALLS_FILE):
        return ""
    return f"[Pitfall · {PITFALLS_FILE}] {neutralize(text)}"


def general_block(view: VaultView, preset: str = "none") -> str:
    text = view.read(PITFALLS_FILE)
    if not text or view.hidden(PITFALLS_FILE, text):
        return ""
    found = [f for f in pitfall_lines(text, preset) if not f["triggers"]]
    ranked = sorted(enumerate(found), key=lambda p: (p[1]["date"], p[0]), reverse=True)
    head = f"[Pitfalls · {PITFALLS_FILE}]"
    lines: list[str] = []
    used = len(CONTEXT_LABEL) + len(head) + 3
    for _, f in ranked[:GENERAL_LINES]:
        line = "- " + neutralize(f["text"])
        if used + len(line) + 1 > GENERAL_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    return CONTEXT_LABEL + ":\n" + "\n".join([head, *lines]) if lines else ""


def render(
    view: VaultView,
    hits: list[Entry],
    claim: Callable[[str], bool],
    budget: int,
    used: int = 0,
) -> str:
    rules: list[str] = []
    notes: list[str] = []
    for hit in hits:
        name, relpath = hit[0], hit[1]
        is_topic = name.startswith("topic-")
        if is_topic:
            block = topic_block(view, name, relpath)
        elif name.startswith(PROCEDURE_PREFIX):
            block = procedure_block(view, relpath)
        elif len(hit) > 3:
            block = pitfall_block(view, hit[3])
        else:
            block = knowledge_block(view, relpath)
        label = RULES_LABEL if is_topic else CONTEXT_LABEL
        extra = len(block) + 1 + (len(label) + 2 if not (rules if is_topic else notes) else 0)
        if not block or used + extra > budget or not claim(name):
            continue
        (rules if is_topic else notes).append(block)
        used += extra
    parts: list[str] = []
    if rules:
        parts.append(RULES_LABEL + ":\n" + "\n".join(rules))
    if notes:
        parts.append(CONTEXT_LABEL + ":\n" + "\n".join(notes))
    return "\n".join(parts)


def ordered(hits: list[Entry]) -> list[Entry]:
    return [h for h in hits if h[0].startswith("topic-")] + [
        h for h in hits if not h[0].startswith("topic-")
    ]
