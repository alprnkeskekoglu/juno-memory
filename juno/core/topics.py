import hashlib
import re
import shlex
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
PITFALL_CAP = 3
EVIDENCE_TOKEN = re.compile(r"[\w./-]+")
EVIDENCE_TAIL = re.compile(r";\s*segment\b.*$", re.I)
# Plain words common enough that hitting them says nothing, alone or together: a pitfall
# fires only when one of its triggers is NOT in this set. Everything else (multi-word terms, flags,
# paths, names with _ . - or digits) counts as specific.
GENERIC_TRIGGERS = frozenset(
    """git gh status run version update upgrade grep juno tests test commit config branch push
    pull log diff add check build install deploy checkout ref tag format zsh bash shell repo
    repos scope file files claude knowledge vault ls cd cat sed echo""".split()
)

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


def is_specific(trigger: str) -> bool:
    """Specific by shape: a flag, path, dotted/hyphenated/underscored/digit name or a phrase.

    A plain alphabetic word is never specific by shape, listed in the stoplist or not.
    """
    if trigger in GENERIC_TRIGGERS:
        return False
    words = trigger.split()
    if len(words) > 1:
        return any(w not in GENERIC_TRIGGERS for w in words)
    return any(not ch.isalpha() for ch in trigger)


def plain_triggers_problem(triggers: list[str]) -> str | None:
    """Doctor warning: no trigger is specific by shape and exactly one is a non-stoplist word."""
    if any(is_specific(t) for t in triggers):
        return None
    if sum(t not in GENERIC_TRIGGERS for t in triggers) != 1:
        return None
    return (
        "triggers are plain words (" + ", ".join(triggers) + "); the pitfall fires only when one "
        "is the command's executable or two of them match; add a flag, path, file name or "
        "hyphenated/underscored term"
    )


def triggers_problem(triggers: list[str]) -> str | None:
    if triggers and all(t in GENERIC_TRIGGERS for t in triggers):
        return (
            "triggers are all generic words (" + ", ".join(triggers) + "); add a specific one "
            "(a flag, a path, a tool or error term) so the pitfall does not fire on every call"
        )
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
    return {
        "text": parts[0].strip(),
        "triggers": triggers[:MAX_TRIGGERS],
        "date": parts[3].strip(),
        "evidence": parts[2].strip(),
    }


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
                            found["evidence"],
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


def matched_triggers(entry: Entry, haystack: str) -> list[str]:
    return [t for t in entry[2] if t in haystack and pattern(t).search(haystack)]


def plain_pitfalls_warning(text: str, preset: str = "none") -> str | None:
    """Doctor warning naming the pitfalls.md lines whose only trigger is a plain word."""
    rows = [
        n
        for n, x in enumerate(text.splitlines(), 1)
        if x.startswith("- ")
        and (f := pitfall_line(x, preset))
        and plain_triggers_problem(f["triggers"])
    ]
    if not rows:
        return None
    shown = ", ".join(map(str, rows[:5])) + (", ..." if len(rows) > 5 else "")
    return (
        f"pitfalls.md: {len(rows)} line{'s' if len(rows) > 1 else ''} ({shown}) with only a "
        "plain-word trigger; it fires only when that word is the command's executable or a "
        "second trigger matches; add a flag, path, file name or hyphenated/underscored trigger"
    )


def is_exe(trigger: str, execs: frozenset[str]) -> bool:
    return trigger in execs and trigger not in GENERIC_TRIGGERS


def pitfall_score(found: list[str], execs: frozenset[str] = frozenset()) -> int:
    return sum(3 if is_specific(t) or is_exe(t, execs) else 1 for t in found)


def pitfall_fires(found: list[str], execs: frozenset[str] = frozenset()) -> bool:
    """One trigger specific by shape or equal to an executable, or two plain non-generic ones."""
    if any(is_specific(t) or is_exe(t, execs) for t in found):
        return True
    return sum(t not in GENERIC_TRIGGERS for t in found) >= 2


def matches(
    entries: list[Entry], text: str, preset: str = "none", execs: frozenset[str] = frozenset()
) -> list[Entry]:
    if not entries or not text:
        return []
    haystack = fold.fold(text[:SCAN_CHARS], preset)
    hits: list[Entry] = []
    spots: list[int] = []
    scores: list[int] = []
    for entry in entries:
        found = matched_triggers(entry, haystack)
        if not found:
            continue
        if len(entry) > 3:
            if not pitfall_fires(found, execs):
                continue
            spots.append(len(hits))
            scores.append(pitfall_score(found, execs))
        hits.append(entry)
    ranked = sorted(range(len(spots)), key=lambda i: -scores[i])
    picked = [hits[spots[i]] for i in ranked]
    for spot, entry in zip(spots, picked):
        hits[spot] = entry
    return hits


def evidence_tokens(evidence: str, preset: str = "none") -> list[str]:
    folded = fold.fold(EVIDENCE_TAIL.sub("", evidence), preset)
    found: list[str] = []
    for token in EVIDENCE_TOKEN.findall(folded):
        token = token.strip("./-")
        if len(token) >= 6 and re.search(r"[_./\d-]", token) and token not in found:
            found.append(token)
    return found


def repeats(entry: Entry, text: str, preset: str = "none") -> bool:
    """True when the call carries a distinctive part of the pitfall's error evidence."""
    if len(entry) < 5:
        return False
    haystack = fold.fold(text[:SCAN_CHARS], preset)
    return any(t in haystack and pattern(t).search(haystack) for t in evidence_tokens(entry[4]))


HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
OPERATORS = frozenset({";", "&&", "||", "|", "&", "(", ")", "<", ">", ">>", "<<", "<<<"})
WRAPPERS = frozenset({"sudo", "env", "time", "command", "nohup", "exec", "xargs", "builtin"})
PROSE_FLAGS = frozenset(
    {"--body", "--body-file", "--message", "--notes", "--notes-file", "--title", "--description"}
)
MESSAGE_EXES = frozenset({"git", "gh", "hg", "jj"})
GREP_EXES = frozenset({"grep", "egrep", "fgrep", "rg", "ag", "ack", "zgrep"})
GREP_VALUE_FLAGS = frozenset(
    {"-A", "-B", "-C", "-m", "-f", "-g", "-t", "--include", "--exclude", "--glob", "--type"}
)
GREP_PATTERN_FLAGS = frozenset({"-e", "--regexp"})
# A grep pattern that is one identifier, path or flag stays; a regex or word list is dropped.
LITERAL_RE = re.compile(r"^[\w./:-]+$")


def strip_heredocs(command: str) -> str:
    lines: list[str] = []
    ending: str | None = None
    for line in command.splitlines():
        if ending is not None:
            if line.strip() == ending:
                ending = None
            continue
        found = HEREDOC_RE.search(line)
        if found:
            ending = found.group(2)
            line = HEREDOC_RE.sub(" ", line)
        lines.append(line)
    return " ; ".join(lines)


def command_segments(command: str) -> list[list[str]]:
    """Tokens per pipeline segment, without heredoc bodies."""
    text = strip_heredocs(command)
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        tokens = text.split()
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in OPERATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [seg for seg in segments if seg]


def segment_exe(tokens: list[str]) -> int | None:
    for i, token in enumerate(tokens):
        if "=" in token and not token.startswith("-") and token.split("=", 1)[0].isidentifier():
            continue
        if token in WRAPPERS or token.startswith("-"):
            continue
        return i
    return None


def own_tokens(tokens: list[str], exe: str) -> list[str]:
    """Drop the text a command carries rather than runs: prose flags, messages, grep patterns."""
    kept: list[str] = []
    skip = False
    pattern_seen = False
    grep = exe in GREP_EXES
    explicit = grep and any(t in GREP_PATTERN_FLAGS or t.startswith("--regexp=") for t in tokens)
    for token in tokens:
        if skip:
            skip = False
            continue
        flag, eq, _ = token.partition("=")
        if flag in PROSE_FLAGS or flag == "--grep" or (grep and flag == "--regexp"):
            skip = not eq
            continue
        if exe in MESSAGE_EXES and token in ("-m", "-F"):
            skip = True
            continue
        if grep:
            if token in GREP_PATTERN_FLAGS:
                skip = True
                continue
            if token in GREP_VALUE_FLAGS:
                kept.append(token)
                skip = True
                continue
            if not token.startswith("-") and not explicit and not pattern_seen:
                pattern_seen = True
                if not LITERAL_RE.match(token):
                    continue
        kept.append(token)
    return kept


def bash_view(command: str) -> tuple[str, frozenset[str]]:
    """The command's own text and its executables (first word of each pipeline segment)."""
    parts: list[str] = []
    execs: set[str] = set()
    for tokens in command_segments(command):
        at = segment_exe(tokens)
        exe = tokens[at].rsplit("/", 1)[-1].lower() if at is not None else ""
        if exe:
            execs.add(exe)
        head = tokens[: (at or 0) + 1]
        parts.append(" ".join(head + own_tokens(tokens[len(head) :], exe)))
    return " ; ".join(parts), frozenset(execs)


def tool_exes(tool: object, tool_in: dict[str, Any]) -> frozenset[str]:
    if tool == "Bash" and isinstance(tool_in.get("command"), str):
        return bash_view(tool_in["command"])[1]
    return frozenset()


def tool_text(tool: object, tool_in: dict[str, Any]) -> str:
    parts = [str(tool or "")]
    if tool == "Bash" and isinstance(tool_in.get("command"), str):
        parts.append(bash_view(tool_in["command"])[0])
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
    again: frozenset[str] = frozenset(),
) -> str:
    rules: list[str] = []
    notes: list[str] = []
    pitfalls = 0
    for hit in hits:
        name, relpath = hit[0], hit[1]
        if len(hit) > 3 and pitfalls >= PITFALL_CAP:
            continue
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
        if not block or used + extra > budget or not (claim(name) or name in again):
            continue
        pitfalls += len(hit) > 3
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
