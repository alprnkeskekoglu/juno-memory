import datetime
import re
from collections.abc import Mapping
from typing import Any

from juno.core import vaultfmt
from juno.core.search import FENCE_RE, KEY_RE, is_repo_name, split_list

DECISION_MARK = "**Decision:**"
CARD_RE = re.compile(
    r"^## (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) · (-|[A-Za-z][A-Za-z0-9]*-[0-9]+) · (.+) · (\S+)$"
)
META_PREFIXES = ("auto:", "Repos:", "Tickets:", "Learned:", DECISION_MARK)
STRICT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.md$")

Item = dict[str, Any]


def lines_of(text: str) -> list[str]:
    return re.findall(r"[^\n]*\n|[^\n]+", text)


def one_line(text: str) -> str:
    return re.sub(r"[\s\x00-\x1f\x7f]+", " ", text).strip()


def inert(text: str) -> str:
    t = one_line(text).replace("<", "&lt;").replace(">", "&gt;")
    return (
        t.replace("[[", "&#91;&#91;")
        .replace("]]", "&#93;&#93;")
        .replace("·", "&middot;")
        .replace("`", "&#96;")
    )


def date_of(value: object) -> str:
    if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    return ""


def split_cards(text: str) -> tuple[str, list[Item]]:
    preamble: list[str] = []
    blocks: list[Item] = []
    current: Item | None = None
    in_fence = False
    for n, line in enumerate(lines_of(text), 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
        m = None if in_fence else CARD_RE.match(line.rstrip("\r\n"))
        if m:
            current = {
                "line": n,
                "date": m.group(1),
                "time": m.group(2),
                "key": m.group(3),
                "label": m.group(4),
                "parts": [line],
            }
            blocks.append(current)
        elif current is None:
            preamble.append(line)
        else:
            current["parts"].append(line)
    for b in blocks:
        b["text"] = "".join(b.pop("parts"))
    return "".join(preamble), blocks


def card_info(block: Item) -> tuple[list[str], list[str], str, list[tuple[int, str]]]:
    repos: list[str] = []
    tickets: list[str] = []
    summary = ""
    decisions: list[tuple[int, str]] = []
    in_fence = False
    for i, line in enumerate(lines_of(block["text"])):
        if i == 0:
            continue
        s = line.strip()
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if s.startswith("Repos:"):
            repos = [t for t in split_list(s[6:]) if is_repo_name(t)]
        elif s.startswith("Tickets:"):
            tickets = [t.upper() for t in split_list(s[8:]) if KEY_RE.fullmatch(t)]
        elif s.startswith(DECISION_MARK):
            text = s[len(DECISION_MARK) :].strip()
            if text:
                decisions.append((block["line"] + i, text))
        elif s and not summary and not s.startswith(META_PREFIXES) and not s.startswith("#"):
            summary = s
    return repos, tickets, summary, decisions


def scan_handoff(rel: str, text: str, cards: list[Item], decisions: list[Item]) -> None:
    stem = rel.rsplit("/", 1)[-1][:-3]
    file_key = stem.upper() if KEY_RE.fullmatch(stem) else None
    preamble, blocks = split_cards(text)
    for n, line in enumerate(lines_of(preamble), 1):
        s = line.strip()
        if s.startswith(DECISION_MARK) and s[len(DECISION_MARK) :].strip():
            decisions.append(
                {
                    "date": "",
                    "line": n,
                    "text": s[len(DECISION_MARK) :].strip(),
                    "path": rel,
                    "ticket": file_key,
                    "repo": None if file_key else stem,
                }
            )
    for b in blocks:
        repos, tickets, summary, decs = card_info(b)
        ticket = b["key"].upper() if b["key"] != "-" else (tickets[0] if tickets else file_key)
        repo = repos[0] if repos else (None if file_key else stem)
        cards.append(
            {
                "path": rel,
                "line": b["line"],
                "date": b["date"],
                "time": b["time"],
                "label": b["label"],
                "ticket": ticket,
                "repo": repo,
                "summary": summary,
            }
        )
        for ln, txt in decs:
            decisions.append(
                {
                    "date": b["date"],
                    "line": ln,
                    "text": txt,
                    "path": rel,
                    "ticket": ticket,
                    "repo": repo,
                }
            )


def norm_list(fields: vaultfmt.Fields, name: str) -> list[str]:
    out: list[str] = []
    for item in vaultfmt.field_items(fields, name):
        found = vaultfmt.WIKILINK_RE.findall(item)
        for tok in found or split_list(item):
            tok = tok.split("|", 1)[0].rsplit("/", 1)[-1].strip()
            if tok:
                out.append(tok)
    return out


def scan_task(rel: str, text: str, tasks: dict[str, Item], decisions: list[Item]) -> None:
    fields, _ = vaultfmt.parse_frontmatter(text)
    key = rel.rsplit("/", 1)[-1][:-3].upper()
    updated = date_of(fields.get("updated"))
    title, status = fields.get("title"), fields.get("status")
    tasks[key] = {
        "path": rel,
        "title": title if isinstance(title, str) else "",
        "status": status if isinstance(status, str) else "",
        "updated": updated,
        "repos": norm_list(fields, "repos"),
    }
    in_fence = False
    for n, line in enumerate(lines_of(text), 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
        s = line.strip()
        if not in_fence and s.startswith(DECISION_MARK) and s[len(DECISION_MARK) :].strip():
            decisions.append(
                {
                    "date": updated,
                    "line": n,
                    "text": s[len(DECISION_MARK) :].strip(),
                    "path": rel,
                    "ticket": key,
                    "repo": None,
                }
            )


def scan(
    handoffs: Mapping[str, str], task_files: Mapping[str, str]
) -> tuple[list[Item], list[Item], dict[str, Item]]:
    cards: list[Item] = []
    decisions: list[Item] = []
    tasks: dict[str, Item] = {}
    for rel in sorted(handoffs):
        scan_handoff(rel, handoffs[rel], cards, decisions)
    for rel in sorted(task_files):
        scan_task(rel, task_files[rel], tasks, decisions)
    return cards, decisions, tasks


def group_of(item: Item) -> str:
    if item["ticket"]:
        return str(item["ticket"])
    return f"repo: {item['repo']}" if item["repo"] else "(no ticket)"


def recap(
    handoffs: Mapping[str, str],
    task_files: Mapping[str, str],
    today: datetime.date,
    days: int = 7,
    ticket: str | None = None,
    name: str = "Juno",
) -> str:
    cutoff = (today - datetime.timedelta(days=days)).isoformat()
    end = today.isoformat()
    want = ticket.upper() if ticket else None
    cards, decisions, tasks = scan(handoffs, task_files)
    groups: dict[str, Item] = {}

    def entry(group: str) -> Item:
        return groups.setdefault(group, {"cards": [], "decisions": [], "status": None})

    def in_window(d: str) -> bool:
        return bool(d) and cutoff <= d <= end

    for c in cards:
        if in_window(c["date"]):
            entry(group_of(c))["cards"].append(c)
    for d in decisions:
        if in_window(d["date"]):
            entry(group_of(d))["decisions"].append(d)
    for key, t in tasks.items():
        if in_window(t["updated"]):
            entry(key)["status"] = t
    if want:
        groups = {k: v for k, v in groups.items() if k == want}
    out = [f"# {name} recap: {cutoff} to {end} ({days} days)", ""]
    if not groups:
        out.append(
            f"No activity for {want} in this window." if want else "No activity in this window."
        )
        return "\n".join(out) + "\n"
    for gname in sorted(groups, key=lambda n: (n.startswith("("), n.startswith("repo:"), n)):
        g = groups[gname]
        task = tasks.get(gname)
        head = f"## {gname}"
        if task:
            head += f" · {inert(task['title']) or gname}"
        out += [head, ""]
        if g["status"]:
            s = g["status"]
            out.append(
                f"Status: updated {s['updated']}, status now {inert(s['status']) or 'unknown'} "
                "(previous status not recorded)"
            )
            out.append("")
        if g["cards"]:
            out.append("Cards:")
            for c in sorted(g["cards"], key=lambda x: (x["date"], x["time"], x["path"], x["line"])):
                summ = f" · {inert(c['summary'])}" if c["summary"] else ""
                out.append(
                    f"- {c['date']} {c['time']} · {inert(c['label'])}{summ} · [[{c['path'][:-3]}]]"
                )
            out.append("")
        if g["decisions"]:
            out.append("Decisions:")
            for d in sorted(g["decisions"], key=lambda x: (x["date"], x["path"], x["line"])):
                out.append(
                    f"- {d['date']} · {inert(d['text'])} · [[{d['path'][:-3]}]] L{d['line']}"
                )
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n"
