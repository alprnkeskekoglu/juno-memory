import json
import re
from dataclasses import dataclass, field
from typing import Any

from juno.core import fold, prompts
from juno.core.ledger import Segment
from juno.core.redact import redact

PROMPT_CHARS = 600
PROMPTS_CHARS = 4000
ASSISTANT_CHARS = 800
ASSISTANT_KEEP = 3
FILES_KEEP = 30
COMMANDS_KEEP = 12
COMMAND_CHARS = 200
RESULT_CHARS = 300
MOMENTS_KEEP = 8
MOMENT_CHARS = 500
MOMENTS_CHARS = 5000
EVENT_CHARS = 6000
MOMENT_QUOTAS = (("user", 4), ("self", 2), ("fix", 2))
BOUNDARY_RE = re.compile(
    r"\bgit\b(?:\s+-[Cc]\s+\S+)*\s+(?:commit|push)\b|\bgh\s+pr\s+(?:create|merge)\b"
)
PR_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
DENIAL_RE = re.compile(
    r"(?i)\b(?:hook (?:denied|blocked|error)|pretooluse[^\n]{0,80}(?:denied|blocked)"
    r"|permission (?:to use \w+ )?(?:has been )?denied|denied by|blocked by)"
)

Candidate = tuple[int, set[int], str]


@dataclass
class Event:
    kind: str
    text: str
    failed: bool = False
    output: str = ""


@dataclass
class Facts:
    files: list[str] = field(default_factory=list)
    boundaries: list[str] = field(default_factory=list)
    assistant: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    bash: dict[str, str] = field(default_factory=dict)


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 1, 0)].rstrip() + "…"


def excerpt(text: str, limit: int, tail: bool = False) -> str:
    text = " ".join(text.split())
    return text[-limit:] if tail else text[:limit]


def add_once(items: list[str], value: str) -> None:
    if value in items:
        items.remove(value)
    items.append(value)


def text_of(body: object) -> str:
    if isinstance(body, str):
        return body
    if isinstance(body, list):
        return "\n".join(
            b["text"] for b in body if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return ""


def result_head(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    if len(lines) > 4:
        lines = [*lines[:2], "...", *lines[-2:]]
    return " | ".join(lines)[:RESULT_CHARS] if lines else "(no output)"


def tool_use(facts: Facts, name: object, args: dict[str, Any], home: str) -> None:
    if name in ("Edit", "Write", "NotebookEdit"):
        path = args.get("file_path") or args.get("notebook_path")
        if isinstance(path, str) and path:
            shown = "~" + path[len(home) :] if home and path.startswith(home + "/") else path
            add_once(facts.files, shown)
    elif (
        name == "Bash"
        and isinstance(args.get("command"), str)
        and BOUNDARY_RE.search(args["command"])
    ):
        add_once(facts.boundaries, " ".join(args["command"].split())[:200])


def entry_item(facts: Facts, kind: object, item: dict[str, Any], home: str) -> None:
    if kind == "assistant" and item.get("type") == "text" and isinstance(item.get("text"), str):
        text = item["text"].strip()
        if text:
            facts.assistant.append(text[:ASSISTANT_CHARS])
            facts.events.append(Event("assistant", text[:EVENT_CHARS]))
    elif kind == "assistant" and item.get("type") == "tool_use":
        args = item.get("input")
        if not isinstance(args, dict):
            return
        tool_use(facts, item.get("name"), args, home)
        command = args.get("command")
        if item.get("name") == "Bash" and isinstance(command, str) and item.get("id"):
            facts.bash[str(item["id"])] = " ".join(command.split())[:COMMAND_CHARS]
    elif kind == "user" and item.get("type") == "tool_result":
        text = text_of(item.get("content"))
        for url in PR_URL_RE.findall(text):
            add_once(facts.boundaries, "PR " + url)
        command = facts.bash.pop(str(item.get("tool_use_id")), None)
        if command:
            failed = bool(item.get("is_error")) or bool(DENIAL_RE.search(text[:300]))
            facts.events.append(Event("bash", command, failed, text))
            mark = " (error)" if item.get("is_error") else ""
            facts.commands.append(f"$ {command}{mark}\n  {result_head(text)}")
            del facts.commands[:-COMMANDS_KEEP]


def typed_event(facts: Facts, entry: dict[str, Any], content: object) -> None:
    if entry.get("type") != "user" or entry.get("isMeta"):
        return
    text = content if isinstance(content, str) else text_of(content) if content else ""
    typed = " ".join(prompts.user_text(text).split()) if text.strip() else ""
    if typed:
        facts.events.append(Event("user", typed[:EVENT_CHARS]))


def facts_from_lines(lines: list[str], home: str) -> Facts:
    facts = Facts()
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        typed_event(facts, entry, content)
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    entry_item(facts, entry.get("type"), item, home)
    facts.assistant = facts.assistant[-ASSISTANT_KEEP:]
    return facts


def cue_lines(text: str, cues: list[str], preset: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if fold.has_cue(line, cues, preset):
            return " ".join(lines[i : i + 2])
    return ""


def error_tail(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-2:])[-RESULT_CHARS:] if lines else "(no output)"


def user_moment(events: list[Event], i: int) -> Candidate:
    prev = next((j for j in range(i - 1, -1, -1) if events[j].kind == "assistant"), None)
    nxt = next((j for j in range(i + 1, len(events)) if events[j].kind == "assistant"), None)
    text = f'user said "{excerpt(events[i].text, 200)}"'
    used = {i}
    if prev is not None:
        text += f'; after assistant: "{excerpt(events[prev].text, 150, tail=True)}"'
        used.add(prev)
    if nxt is not None:
        text += f'; then assistant: "{excerpt(events[nxt].text, 150)}"'
        used.add(nxt)
    return i, used, text


def fix_moment(events: list[Event], failed: int, fixed: int) -> Candidate:
    text = (
        f"command failed: $ {events[failed].text[:COMMAND_CHARS]} -> "
        f"{error_tail(events[failed].output)}; fixed by: $ {events[fixed].text[:COMMAND_CHARS]}"
    )
    return fixed, {failed, fixed}, text


def moment_candidates(
    events: list[Event], user_cues: list[str], self_cues: list[str], preset: str
) -> dict[str, list[Candidate]]:
    found: dict[str, list[Candidate]] = {"user": [], "self": [], "fix": []}
    pending: int | None = None
    for i, event in enumerate(events):
        if event.kind == "user" and fold.has_cue(event.text, user_cues, preset):
            found["user"].append(user_moment(events, i))
        elif event.kind == "assistant" and fold.has_cue(event.text, self_cues, preset):
            said = excerpt(cue_lines(event.text, self_cues, preset), 300)
            found["self"].append((i, {i}, f'assistant admitted: "{said}"'))
        elif event.kind == "bash" and event.failed:
            pending = i
        elif event.kind == "bash" and pending is not None:
            found["fix"].append(fix_moment(events, pending, i))
            pending = None
    return found


def spread[T](items: list[T], count: int) -> list[T]:
    if count <= 0:
        return []
    if len(items) <= count:
        return list(items)
    return [items[(i * len(items)) // count + len(items) // (2 * count)] for i in range(count)]


def pick_moments(found: dict[str, list[Candidate]]) -> list[Candidate]:
    kept: list[Candidate] = []
    used: set[int] = set()

    def take(kind: str, count: int) -> None:
        free = [c for c in found[kind] if not c[1] & used and c not in kept]
        for cand in spread(free, count):
            if len(kept) < MOMENTS_KEEP and not cand[1] & used:
                kept.append(cand)
                used.update(cand[1])

    for kind, quota in MOMENT_QUOTAS:
        take(kind, quota)
    for kind, _ in MOMENT_QUOTAS:
        take(kind, MOMENTS_KEEP - len(kept))
    return sorted(kept, key=lambda c: c[0])


def learning_moments(
    events: list[Event], user_cues: list[str], self_cues: list[str], preset: str
) -> str:
    lines: list[str] = []
    size = 0
    for _, _, text in pick_moments(moment_candidates(events, user_cues, self_cues, preset)):
        line = "- " + text[:MOMENT_CHARS]
        if size + len(line) > MOMENTS_CHARS:
            break
        lines.append(line)
        size += len(line)
    if not lines:
        return ""
    return (
        "Learning moments (user corrections, assistant self-corrections, failed commands later "
        "fixed; oldest first):\n" + "\n".join(lines)
    )


def typed_prompts(seg: Segment) -> str:
    texts = [" ".join(t.split())[:PROMPT_CHARS] for t in seg.prompts.values() if t.strip()]
    kept: list[str] = []
    size = 0
    for text in reversed(texts):
        if size + len(text) > PROMPTS_CHARS and kept:
            break
        kept.append(text)
        size += len(text)
    skipped = len(texts) - len(kept)
    lines = ["- " + t for t in reversed(kept)]
    note = [f"- ({skipped} earlier prompts omitted)"] if skipped else []
    return "\n".join(note + lines) or "-"


def build_digest(
    seg: Segment,
    facts: Facts,
    existing: str,
    limit: int,
    user_cues: list[str],
    self_cues: list[str],
) -> str:
    preset = seg.rules.preset
    parts = [
        f"Segment {seg.sid[:8]}; title: {seg.title or '-'}; repos: {', '.join(seg.repos) or '-'}; "
        f"tickets: {', '.join(seg.tickets) or '-'}; {seg.edit_total} edits, {seg.tools} tool "
        f"calls, {seg.turns} user turns.",
        "Typed user prompts (oldest first):\n" + typed_prompts(seg),
    ]
    if facts.files:
        parts.append("Files edited:\n" + "\n".join("- " + f for f in facts.files[-FILES_KEEP:]))
    if facts.boundaries:
        parts.append(
            "Commits, pushes and PRs:\n" + "\n".join("- " + b for b in facts.boundaries[-15:])
        )
    assistant = facts.assistant or ([seg.last_msg] if seg.last_msg else [])
    if assistant:
        parts.append("Last assistant messages (oldest first):\n" + "\n---\n".join(assistant))
    if existing:
        parts.append(
            "This segment's current card (rewrite it with the new work, keeping its concrete "
            "details):\n" + existing
        )
    if facts.commands:
        parts.append(
            "Bash commands with the head and tail of their output (oldest first):\n"
            + "\n".join(facts.commands[-COMMANDS_KEEP:])
        )
    moments = redact(learning_moments(facts.events, user_cues, self_cues, preset))
    moments = clip(moments, limit // 3) if moments else ""
    body = clip(redact("\n\n".join(parts)), limit - len(moments) - 2 if moments else limit)
    return body + "\n\n" + moments if moments else body
