import re
from collections import Counter
from collections.abc import Callable
from typing import Any

from juno.core import fold, privacy, topics
from juno.core.extract import (
    FACTS_CONSIDERED,
    FACTS_PER_FILE,
    FACTS_PER_RUN,
    PITFALLS_CONSIDERED,
    PITFALLS_PER_RUN,
    THREAD_CHARS,
    THREAD_CLOSES,
    THREAD_LINES,
    THREAD_OPENS,
    THREAD_TAG_LINES,
    TITLE_CHARS,
    check_fact,
    clean_triggers,
    pitfall_item,
    related,
    thread_tags,
)
from juno.core.textchecks import SECOND_PERSON_RE, as_list, text_field

GOTCHAS_CAP = 25
KNOWLEDGE_FILE_CHARS = 8000
PITFALLS_HEADER = [
    "# Pitfalls",
    "Claude's own recurring mistakes and the correct behaviour, written by the session-end "
    "card run. Delete a wrong line by hand.",
    "",
]


def new_profile(repo: str, shown_path: str, today: str) -> str:
    return (
        f"---\nrepo: {repo}\npath: {shown_path}\nstack: unknown\nupdated: {today}\n---\n"
        "## Purpose\nNot described yet.\n\n## How to run\nNot described yet.\n\n"
        "## Gotchas\n\n## Links\n\n## Tickets\n"
        "<!-- juno:tickets:start -->\n_No tickets._\n<!-- juno:tickets:end -->\n"
    )


def body_start(text: str) -> int:
    match = re.match(r"^---[ \t]*\n.*?\n---[ \t]*(?:\n|$)", text, re.S)
    return match.end() if match else 0


def set_field(text: str, name: str, value: str) -> str:
    end = body_start(text)
    if not end:
        return text
    head = re.sub(rf"(?m)^{re.escape(name)}:.*$", f"{name}: {value}", text[:end], count=1)
    return head + text[end:]


def add_sources(text: str, card_files: list[str]) -> str:
    end = body_start(text)
    if not end or not card_files:
        return text
    head = text[:end]
    match = re.search(r"(?m)^sources:[ \t]*\[(.*)\][ \t]*$", head)
    if match:
        items = [s.strip() for s in match.group(1).split(",") if s.strip()]
        items += [f for f in card_files if f not in items]
        head = head[: match.start()] + f"sources: [{', '.join(items)}]" + head[match.end() :]
    elif not re.search(r"(?m)^sources:", head):
        close = head.rstrip().rfind("\n")
        head = head[: close + 1] + f"sources: [{', '.join(card_files)}]\n" + head[close + 1 :]
    return head + text[end:]


def add_gotcha(text: str, line: str) -> tuple[str | None, str | None]:
    head = re.search(r"^## Gotchas[ \t]*$\n?", text, re.M)
    if not head:
        return text.rstrip("\n") + "\n\n## Gotchas\n" + line + "\n", None
    nxt = re.search(r"^## ", text[head.end() :], re.M)
    end = head.end() + nxt.start() if nxt else len(text)
    block = text[head.end() : end]
    if sum(1 for row in block.splitlines() if row.startswith("- ")) >= GOTCHAS_CAP:
        return None, "full"
    body = block.rstrip("\n")
    tail = "\n\n" if nxt else "\n"
    return (
        text[: head.end()] + (body + "\n" if body else "") + line + tail + text[end:].lstrip("\n"),
        None,
    )


def add_knowledge(text: str, line: str) -> str:
    start = body_start(text)
    lines = text[start:].split("\n")
    bullets = [i for i, row in enumerate(lines) if row.startswith("- ")]
    links = [i for i, row in enumerate(lines) if row.startswith("Links:")]
    if bullets:
        at = bullets[-1] + 1
    elif links:
        at = links[0]
        line += "\n"
    else:
        while lines and not lines[-1].strip():
            lines.pop()
        lines.append("")
        at = len(lines)
    lines.insert(at, line)
    body = "\n".join(lines)
    return text[:start] + body + ("" if body.endswith("\n") else "\n")


def new_knowledge(
    item: dict[str, Any], line: str, repos: list[str], card_files: list[str], today: str
) -> tuple[str | None, str | None]:
    title = text_field(item.get("title"))
    if not TITLE_CHARS[0] <= len(title) <= TITLE_CHARS[1] or privacy.scan(title) or "·" in title:
        return None, "no title"
    known = {r.casefold(): r for r in repos}
    named = [
        known[r.casefold()]
        for r in (str(v) for v in as_list(item.get("repos")))
        if r.casefold() in known
    ]
    fields = [
        "---",
        f"repos: [{', '.join(dict.fromkeys(named))}]",
        "tickets: []",
        f"updated: {today}",
        f"sources: [{', '.join(card_files)}]",
    ]
    triggers = clean_triggers(item.get("triggers"))
    if triggers:
        fields.append(f"triggers: [{', '.join(triggers)}]")
    return "\n".join([*fields, "---", "# " + title, "", line]) + "\n", None


def place_fact(
    rel: str,
    text: str,
    item: dict[str, Any],
    fact: dict[str, str],
    *,
    sid8: str,
    repos: list[str],
    card_files: list[str],
    today: str,
    profile_path: str,
) -> tuple[str | None, str | None]:
    line = f"- {fact['text'].rstrip('.')} ({fact['evidence']}; segment {sid8})."
    if rel.startswith("projects/"):
        repo = next(r for r in repos if r.casefold() == rel[9:-3].casefold())
        base = text if text.strip() else new_profile(repo, profile_path, today)
        updated, problem = add_gotcha(base, line)
    elif text.strip():
        updated, problem = add_knowledge(text, line), None
    else:
        updated, problem = new_knowledge(item, line, repos, card_files, today)
    if updated is None:
        return None, problem
    if len(updated) > KNOWLEDGE_FILE_CHARS and rel.startswith("knowledge/"):
        return None, "full"
    updated = set_field(updated, "updated", today)
    if rel.startswith("knowledge/"):
        updated = add_sources(updated, card_files)
    return updated, None


def apply_facts(
    items: list[object],
    digest: str,
    repos: list[str],
    read: Callable[[str], str],
    *,
    sid8: str,
    card_files: list[str],
    today: str,
    profile_path: Callable[[str], str],
    allowed: Callable[[str], bool],
) -> tuple[dict[str, str], list[dict[str, str]], dict[str, int]]:
    written: list[dict[str, str]] = []
    dropped: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    texts: dict[str, str] = {}
    if len(items) > FACTS_CONSIDERED:
        dropped["capped"] += len(items) - FACTS_CONSIDERED
    for item in items[:FACTS_CONSIDERED]:
        problem, rel, fact = check_fact(item, digest, repos)
        updated: str | None = None
        if problem is None and not allowed(rel):
            problem = "path"
        if problem is None:
            base = texts[rel] if rel in texts else read(rel)
            if fold.fold(fact["text"].rstrip(".")) in fold.fold(base):
                problem = "duplicate"
            elif len(written) >= FACTS_PER_RUN or counts[rel] >= FACTS_PER_FILE:
                problem = "capped"
            else:
                shown = profile_path(rel[9:-3]) if rel.startswith("projects/") else ""
                assert isinstance(item, dict)
                updated, problem = place_fact(
                    rel,
                    base,
                    item,
                    fact,
                    sid8=sid8,
                    repos=repos,
                    card_files=card_files,
                    today=today,
                    profile_path=shown,
                )
        if problem or updated is None:
            dropped[problem or "invalid"] += 1
            continue
        texts[rel] = updated
        counts[rel] += 1
        written.append({"file": rel, "text": fact["text"][:160]})
    return texts, written, dict(dropped)


def apply_pitfalls(
    text: str, items: list[object], digest: str, sid8: str, today: str
) -> tuple[str | None, list[dict[str, Any]], dict[str, int]]:
    written: list[dict[str, Any]] = []
    dropped: Counter[str] = Counter()
    if len(items) > PITFALLS_CONSIDERED:
        dropped["capped"] += len(items) - PITFALLS_CONSIDERED
    lines = text.splitlines()
    known = {fold.fold(f["text"].rstrip(".")) for f in topics.pitfall_lines(text)}
    for item in items[:PITFALLS_CONSIDERED]:
        problem, value = pitfall_item(item, digest)
        key = fold.fold(value["text"].rstrip(".")) if value else ""
        if problem is None and key in known:
            problem = "duplicate"
        elif problem is None and len(written) >= PITFALLS_PER_RUN:
            problem = "capped"
        if problem or value is None:
            dropped[problem or "invalid"] += 1
            continue
        trig = ", ".join(value["triggers"]) or "-"
        lines.append(
            f"- {value['text']} · triggers: {trig} · {value['evidence']}; segment {sid8} · {today}"
        )
        known.add(key)
        written.append({"text": value["text"][:160], "triggers": value["triggers"]})
    if not written:
        return None, written, dict(dropped)
    if not text.strip():
        lines = PITFALLS_HEADER + [row for row in lines if row.startswith("- ")]
    return "\n".join(lines) + "\n", written, dict(dropped)


def closable(lines: list[str], raw: object, tags: dict[str, str]) -> int | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    wanted = raw.strip()
    for index, line in enumerate(lines):
        if line.rstrip() == wanted and related(line, tags):
            return index
    return None


def open_line(lines: list[str], item: object, tags: dict[str, str], today: str) -> str | None:
    if not isinstance(item, dict):
        return None
    tag = tags.get(text_field(item.get("tag")).casefold())
    text = text_field(item.get("text"))
    bad = "·" in text or "[" in text or privacy.scan(text) or SECOND_PERSON_RE.search(text)
    if not tag or not THREAD_CHARS[0] <= len(text) <= THREAD_CHARS[1] or bad:
        return None
    open_lines = [row for row in lines if row.startswith("- ")]
    if fold.fold(text) in fold.fold("\n".join(open_lines)) or len(open_lines) >= THREAD_LINES:
        return None
    if sum(1 for row in open_lines if tag.casefold() in thread_tags(row)) >= THREAD_TAG_LINES:
        return None
    return f"- [{tag}] {text} · since {today}"


def apply_threads(
    text: str, threads: dict[str, Any], tags: dict[str, str], today: str
) -> tuple[str | None, dict[str, Any]]:
    out: dict[str, Any] = {"opened": [], "closed": [], "dropped": 0}
    opens, closes = as_list(threads.get("open")), as_list(threads.get("close"))
    lines = text.splitlines()
    out["dropped"] = max(0, len(closes) - THREAD_CLOSES) + max(0, len(opens) - THREAD_OPENS)
    for raw in closes[:THREAD_CLOSES]:
        index = closable(lines, raw, tags)
        if index is None:
            out["dropped"] += 1
        else:
            out["closed"].append(lines.pop(index))
    for item in opens[:THREAD_OPENS]:
        line = open_line(lines, item, tags, today)
        if line is None:
            out["dropped"] += 1
        else:
            lines.append(line)
            out["opened"].append(line)
    if not (out["opened"] or out["closed"]):
        return None, out
    if not text.strip():
        lines = ["# Threads", "", *lines]
    return "\n".join(lines) + "\n", out
