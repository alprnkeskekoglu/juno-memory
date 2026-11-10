import contextlib
import re
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from juno.core import privacy, topics
from juno.core.vaultfmt import (
    CARD_HEAD_RE,
    WIKILINK_RE,
    card_field,
    injected_rules,
    rule_entries,
)

Check = tuple[str, str, str]
BUDGET_WARN_RATIO = 0.9
HANDOFF_CARDS = 20
NOTE_LINES, NOTE_CHARS = 60, 4000
KNOWLEDGE_CHARS = 8000
THREAD_LINES, THREAD_LINE_CHARS = 30, 200
PRIVACY_SHOWN = 5
LINK_SHOWN = 20
STALE_SHOWN = 5
FAST_PATH_MS = 50.0
TICKET_FILE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-[0-9]+$")
FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
CODE_RE = re.compile(r"`[^`\n]*`")


def card_checks(cards: Sequence[Mapping[str, Any]]) -> list[Check]:
    out: list[Check] = []
    bad = [c for c in cards if not c["valid"]]
    for card in bad:
        out.append(
            (
                "FAIL",
                "cards",
                f"{card['path']}: heading is not a card heading: {str(card['head'])[:80]}",
            )
        )
    valid = [c for c in cards if c["valid"]]
    for card in valid:
        missing = [
            f for f in ("Repos", "Tickets", "Learned") if card_field(str(card["body"]), f) is None
        ]
        if missing:
            out.append(
                ("FAIL", "cards", f"{card['path']}: card {card['seg']} lacks {', '.join(missing)}")
            )
    if not bad:
        out.append(("PASS", "cards", f"{len(cards)} card headings parsed"))
    for card in valid:
        if card.get("auto"):
            out.append(
                ("WARN", "stubs", f"{card['path']}: stub card {card['seg']} waits for a real card")
            )
    counts = Counter((c["path"], c["seg"]) for c in valid)
    for (path, seg), n in sorted(counts.items()):
        if n > 1:
            out.append(("WARN", "duplicate cards", f"{path}: {n} cards for segment {seg}"))
    return out


def rule_checks(
    files: Mapping[str, str],
    global_lines: int,
    scoped_lines: int,
    preset: str,
) -> list[Check]:
    out: list[Check] = []
    for name, text in sorted(files.items()):
        entries = rule_entries(text)
        for entry in entries:
            if entry.text is None:
                out.append(
                    (
                        "FAIL",
                        "rules",
                        f"rules/{name}:{entry.number} has no valid provenance: {entry.line[:80]}",
                    )
                )
        cap = global_lines if name == "global.md" else scoped_lines
        if len(entries) > cap:
            out.append(
                (
                    "FAIL",
                    "rules",
                    f"rules/{name} has {len(entries)} lines (cap {cap}); "
                    f"oldest: line {entries[0].number}",
                )
            )
        stem = name[:-3]
        top_level = "/" not in name
        if top_level and TICKET_FILE_RE.fullmatch(stem):
            out.append(
                (
                    "WARN",
                    "rules",
                    f"rules/{name} holds legacy ticket rules; move them with `juno rule move`",
                )
            )
        elif top_level and name != "global.md":
            out.append(
                (
                    "WARN",
                    "rules",
                    f"rules/{name} is outside rules/repos/ and rules/topics/; "
                    "move it under rules/repos/ or rules/topics/",
                )
            )
        elif name.startswith("topics/") and not topics.topic_slug(name):
            out.append(("WARN", "rules", f"rules/{name} is not a valid topic file name"))
        elif topics.topic_slug(name) and not topics.file_triggers(text, preset):
            out.append(
                (
                    "WARN",
                    "rules",
                    f"rules/{name} has no triggers: line, so its rules are never injected",
                )
            )
    out.append(("PASS", "rules", f"{len(files)} rule files checked"))
    return out


def budget_checks(core_text: str, global_text: str, core_cap: int, global_cap: int) -> list[Check]:
    size = len(injected_rules(global_text))
    core_size = len(core_text.strip())
    out: list[Check] = []
    if size > global_cap:
        out.append(("FAIL", "budget", f"global rules inject {size} chars (cap {global_cap})"))
    if core_size > core_cap:
        out.append(("FAIL", "budget", f"core.md is {core_size} chars (cap {core_cap})"))
    for name, used, cap in (("core.md", core_size, core_cap), ("global rules", size, global_cap)):
        if cap >= used >= cap * BUDGET_WARN_RATIO:
            out.append(
                (
                    "WARN",
                    "budget",
                    f"{name} is {used} of {cap} chars; past the cap the hook cuts its tail",
                )
            )
    if not out:
        out.append(("PASS", "budget", f"core.md {core_size} chars, global rules {size} chars"))
    return out


def profile_checks(repos: Sequence[str], profiles: set[str]) -> list[Check]:
    missing = sorted(r for r in set(repos) if r not in profiles)
    if missing:
        return [("WARN", "profiles", "repos in cards without a profile: " + ", ".join(missing))]
    return [("PASS", "profiles", "every repo in cards has a profile")]


def handoff_size_checks(counts: Mapping[str, int]) -> list[Check]:
    over = [(n, c) for n, c in sorted(counts.items()) if c > HANDOFF_CARDS]
    if not over:
        return [("PASS", "handoffs", f"no handoff file over {HANDOFF_CARDS} cards")]
    return [
        ("WARN", "handoffs", f"handoffs/{n} has {c} cards (over {HANDOFF_CARDS}); run juno compact")
        for n, c in over
    ]


def learned_check(stale: bool | None) -> list[Check]:
    if stale is None:
        return [("INFO", "learned", "_generated/learned.md not built yet (juno digest)")]
    if stale:
        return [
            (
                "WARN",
                "learned",
                "_generated/learned.md is older than the newest handoff; run juno digest",
            )
        ]
    return [("PASS", "learned", "_generated/learned.md is current")]


def retire_checks(
    pairs: Sequence[tuple[str, str]], dead: Sequence[Mapping[str, object]]
) -> list[Check]:
    out: list[Check] = [("PASS", "retired", f"retired {t} by {s}") for t, s in pairs]
    out += [
        (
            "WARN",
            "supersedes",
            f"dead supersedes in {d.get('path')}: {d.get('ref')} ({d.get('reason')})",
        )
        for d in dead
    ]
    if not out:
        out.append(("PASS", "supersedes", "no retirements, no dead supersedes"))
    return out


def without_seg(text: str) -> str:
    """Drop the trailing ` · <seg8>` of card headings, as cardfmt.validate_card does."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        head = CARD_HEAD_RE.match(line)
        if head and re.fullmatch(r"[0-9a-f]{8}", head.group(4)):
            lines[i] = line[: head.start(4) - 3]
    return "\n".join(lines)


def privacy_checks(files: Mapping[str, str], prefixes: Sequence[str] | None = None) -> list[Check]:
    hits: list[tuple[str, list[str]]] = []
    for rel, text in sorted(files.items()):
        found = privacy.kinds(without_seg(text), prefixes)
        if found:
            hits.append((rel, found))
    out: list[Check] = [
        (
            "WARN",
            "privacy",
            f"{rel}: possible {', '.join(sorted(set(kinds)))} in the vault; remove it",
        )
        for rel, kinds in hits[:PRIVACY_SHOWN]
    ]
    if len(hits) > PRIVACY_SHOWN:
        out.append(
            (
                "WARN",
                "privacy",
                f"{len(hits) - PRIVACY_SHOWN} more files with possible private data",
            )
        )
    return out or [("PASS", "privacy", "no secret, email, phone or id pattern in the vault")]


def note_checks(notes: Mapping[str, str]) -> list[Check]:
    out: list[Check] = []
    for name, text in sorted(notes.items()):
        if len(text.splitlines()) > NOTE_LINES or len(text) > NOTE_CHARS:
            out.append(
                (
                    "WARN",
                    "notes",
                    f"notes/{name} is over {NOTE_LINES} lines or {NOTE_CHARS} chars; trim it",
                )
            )
    return out or [("PASS", "notes", f"no note over {NOTE_LINES} lines or {NOTE_CHARS} chars")]


def knowledge_checks(files: Mapping[str, tuple[str, bool]]) -> list[Check]:
    out: list[Check] = []
    for name, (text, has_sources) in sorted(files.items()):
        if len(text) > KNOWLEDGE_CHARS:
            out.append(
                (
                    "WARN",
                    "knowledge",
                    f"knowledge/{name} has {len(text)} chars (over {KNOWLEDGE_CHARS}); split it",
                )
            )
        if not has_sources:
            out.append(("WARN", "knowledge", f"knowledge/{name} has no sources: list"))
    return out or [("PASS", "knowledge", "knowledge files within size and sources checks")]


def pitfall_checks(text: str, preset: str) -> list[Check]:
    if not text:
        return [("PASS", "pitfalls", "no pitfalls.md lines")]
    lines = [(n, x) for n, x in enumerate(text.splitlines(), 1) if x.startswith("- ")]
    bad = [
        n
        for n, x in lines
        if len(x) > topics.PITFALL_LINE_CHARS or topics.pitfall_line(x, preset) is None
    ]
    out: list[Check] = [
        (
            "WARN",
            "pitfalls",
            f"pitfalls.md:{n} is malformed (expected '- text · triggers: a, b · evidence · date')",
        )
        for n in bad
    ]
    return out or [("PASS", "pitfalls", f"pitfalls.md: {len(lines)} lines")]


def thread_checks(text: str, stale_days: int, pending: int, now: float) -> list[Check]:
    out: list[Check] = []
    lines = [(n, x) for n, x in enumerate(text.splitlines(), 1) if x.startswith("- ")]
    if len(lines) > THREAD_LINES:
        out.append(
            ("WARN", "threads", f"threads.md has {len(lines)} open lines (over {THREAD_LINES})")
        )
    cutoff = now - stale_days * 86400
    for number, line in lines:
        if len(line) > THREAD_LINE_CHARS:
            out.append(
                (
                    "WARN",
                    "threads",
                    f"threads.md:{number} is {len(line)} chars (over {THREAD_LINE_CHARS})",
                )
            )
        since = re.search(r"since (\d{4}-\d{2}-\d{2})\s*$", line)
        old = False
        if since:
            with contextlib.suppress(ValueError):
                old = time.mktime(time.strptime(since.group(1), "%Y-%m-%d")) < cutoff
        if old and since:
            out.append(
                (
                    "WARN",
                    "threads",
                    f"threads.md:{number} is older than {stale_days} days (since {since.group(1)})",
                )
            )
    if not out:
        out.append(("PASS", "threads", f"threads.md: {len(lines)} open lines"))
    if pending:
        out.append(
            ("INFO", "threads", f"{pending} thread close candidates pending (juno thread pending)")
        )
    return out


def synthetic_checks(found: Mapping[str, int]) -> list[Check]:
    if found["records"]:
        return [
            (
                "WARN",
                "synthetic",
                f"{found['records']} stored prompt records ({found['prompts']} prompts, "
                f"{found['ledgers']} ledgers) are harness-generated; "
                "run juno doctor --purge-synthetic",
            )
        ]
    return [("PASS", "synthetic", "no harness-generated prompts in the segment ledgers")]


def backlog_checks(
    rows: Sequence[Mapping[str, object]], today: str, pending: int, closed: Sequence[str]
) -> list[Check]:
    if not rows:
        return [("PASS", "backlog", "no backlog items")]
    out: list[Check] = [
        (
            "WARN",
            "backlog",
            f"backlog/{r['slug']}.md has an invalid frontmatter ({r['problem']}); fix it by hand",
        )
        for r in rows
        if r["problem"]
    ]
    live = [r for r in rows if not r["problem"] and r["status"] not in closed]
    titles = Counter(str(r["title"]).casefold() for r in live)
    for title, n in sorted(titles.items()):
        if n > 1:
            out.append(("WARN", "backlog", f"{n} open items share the title {title!r}"))
    overdue = sum(1 for r in live if r["due"] and str(r["due"]) < today)
    counts = ", ".join(
        f"{p} {sum(1 for r in live if r['priority'] == p)}" for p in ("p1", "p2", "p3")
    )
    out.append(
        (
            "PASS",
            "backlog",
            f"{len(live)} open ({counts}), {overdue} overdue, {pending} pending suggestions",
        )
    )
    return out


def stale_item_checks(
    live: Sequence[Mapping[str, object]], today_ts: float, days: int
) -> list[Check]:
    out: list[Check] = []
    for r in live:
        try:
            updated = time.mktime(time.strptime(str(r["updated"]), "%Y-%m-%d"))
        except ValueError:
            continue
        if today_ts - updated > days * 86400:
            out.append(
                ("WARN", "backlog", f"backlog/{r['slug']}.md not updated for over {days} days")
            )
    return out


def stale_cache_checks(stale: Sequence[str] | None, checked: str | None) -> list[Check]:
    if stale is None:
        return [("INFO", "stale cache", "no stale-memory check yet (juno stale)")]
    if not stale:
        return [
            ("PASS", "stale cache", f"no knowledge/profile file flagged stale (checked {checked})")
        ]
    more = f" and {len(stale) - STALE_SHOWN} more" if len(stale) > STALE_SHOWN else ""
    return [
        (
            "WARN",
            "stale cache",
            f"{len(stale)} files may be stale: {', '.join(sorted(stale)[:STALE_SHOWN])}{more}",
        )
    ]


def link_targets(text: str) -> list[tuple[int, str]]:
    cleaned = CODE_RE.sub(
        lambda m: " " * len(m.group(0)),
        FENCE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text),
    )
    found: list[tuple[int, str]] = []
    for number, line in enumerate(cleaned.splitlines(), 1):
        for raw in WIKILINK_RE.findall(line):
            target = raw.split("|", 1)[0].split("#", 1)[0].strip()
            if target and "<" not in target and "..." not in target:
                found.append((number, target))
    return found


def resolve_link(target: str, rels: set[str], names: set[str]) -> bool:
    low = target.casefold().removesuffix(".md")
    if "/" in low:
        return low in rels
    return low in names


def link_checks(files: Mapping[str, str]) -> list[Check]:
    rels = {r[:-3].casefold() for r in files}
    names = {r.rsplit("/", 1)[-1] for r in rels}
    broken: list[tuple[str, int, str]] = []
    total = 0
    for rel, text in sorted(files.items()):
        for number, target in link_targets(text):
            total += 1
            if not resolve_link(target, rels, names):
                broken.append((rel, number, target))
    if not broken:
        return [("PASS", "links", f"{total} wikilinks in {len(files)} files resolve")]
    out: list[Check] = [
        ("WARN", "links", f"broken link {r}:{n} -> [[{t}]]") for r, n, t in broken[:LINK_SHOWN]
    ]
    if len(broken) > LINK_SHOWN:
        out.append(("WARN", "links", f"{len(broken) - LINK_SHOWN} more broken links not shown"))
    ghosts = Counter(t for _, _, t in broken)
    ranked = sorted(ghosts.items(), key=lambda item: (-item[1], item[0]))[:LINK_SHOWN]
    out.append(
        (
            "WARN",
            "links",
            f"{len(ghosts)} ghost link targets: " + ", ".join(f"{t} ({n})" for t, n in ranked),
        )
    )
    return out


def percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def timing_checks(lines: Sequence[str]) -> list[Check]:
    by_event: dict[str, list[float]] = {}
    for line in lines:
        match = re.fullmatch(r"(\w+) (\d+)ms", line.strip())
        if match:
            by_event.setdefault(match.group(1), []).append(float(match.group(2)))
    if not by_event:
        return [("INFO", "hook timings", "no hook timing log yet")]
    summary = ", ".join(
        f"{e} n={len(v)} p50={percentile(v, 0.5):.0f}ms p95={percentile(v, 0.95):.0f}ms"
        for e, v in sorted(by_event.items())
    )
    slow = "PreToolUse" in by_event and percentile(by_event["PreToolUse"], 0.5) >= FAST_PATH_MS
    return [("WARN" if slow else "PASS", "hook timings", summary)]


def probe_check(times_ms: Sequence[float]) -> Check:
    median = sorted(times_ms)[len(times_ms) // 2]
    return (
        "PASS" if median < FAST_PATH_MS else "WARN",
        "pretooluse",
        f"fast path median {median:.1f} ms",
    )


def error_checks(count: int, last: Mapping[str, str] | None, repeated: int) -> list[Check]:
    if count:
        kind = f"; last: {last['ts']} {last['kind']}" if last else ""
        level = "WARN"
        return [
            (
                level,
                "hook errors",
                f"{count} hook errors in the last 24 h{kind}"
                + (f"; {repeated} repeated" if repeated else ""),
            )
        ]
    return [("PASS", "hook errors", "no hook errors in the last 24 h")]


def number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def card_run_checks(rows: Sequence[Mapping[str, Any]], mode: str, now: float) -> list[Check]:
    recent = [r for r in rows if now - number(r.get("t")) <= 7 * 86400]
    if not recent:
        return [("INFO", "card runs", f"no card runs in the last 7 days (handoff mode {mode})")]
    failed = [r for r in recent if r.get("status") == "failed"]
    ok = sum(1 for r in recent if r.get("status") == "ok")
    cost = sum(number(r.get("cost_usd")) for r in recent)
    text = (
        f"card runs (7 days, handoff mode {mode}): {ok} ok, {len(failed)} failed, "
        f"{len(recent) - ok - len(failed)} skipped, ${cost:.2f}"
    )
    out: list[Check] = [("WARN" if failed else "PASS", "card runs", text)]
    for row in failed[-3:]:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(number(row.get("t"))))
        out.append(
            (
                "WARN",
                "card runs",
                f"card run {str(row.get('sid'))[:8]} failed at {stamp}: {row.get('error') or '-'}",
            )
        )
    return out


def learning_checks(
    rows: Sequence[Mapping[str, Any]], pending: Mapping[str, int], now: float
) -> list[Check]:
    recent = [r for r in rows if now - number(r.get("t")) <= 7 * 86400]

    def total(key: str, sub: str) -> int:
        return int(sum(number(r[key].get(sub)) for r in recent if isinstance(r.get(key), dict)))

    knowledge = sum(len(r["knowledge"]) for r in recent if isinstance(r.get("knowledge"), list))
    opened = sum(
        len(r["threads"].get("opened") or []) for r in recent if isinstance(r.get("threads"), dict)
    )
    closed = sum(
        len(r["threads"].get("closed") or []) for r in recent if isinstance(r.get("threads"), dict)
    )
    text = (
        f"session-end learning (7 days): rule candidates {total('rules', 'queued')} queued, "
        f"{total('rules', 'unverified')} unverified, {total('rules', 'duplicate')} duplicate, "
        f"{total('rules', 'invalid')} invalid; pending rules {pending['rules']}, threads "
        f"{pending['threads']}, backlog {pending['backlog']}; {knowledge} knowledge writes; "
        f"threads {opened} opened, {closed} closed"
    )
    out: list[Check] = [("PASS", "learning", text)]
    for row in [r for r in recent if r.get("status") == "ok" and r.get("extract_error")][-2:]:
        out.append(
            (
                "WARN",
                "learning",
                f"card run {str(row.get('sid'))[:8]} extraction: {row['extract_error']}",
            )
        )
    return out


def shadow_checks(enabled: bool, stamps: Sequence[float] | None, bad: int) -> list[Check]:
    state = "on" if enabled else "off"
    if stamps is None:
        return [("INFO", "shadow", f"shadow mode {state}; no shadow entries yet")]
    text = f"shadow mode {state}; {len(stamps)} shadow entries"
    if stamps:
        first, last = min(stamps), max(stamps)
        days = max(1.0, (last - first) / 86400)
        text += (
            f", last entry {time.strftime('%Y-%m-%d %H:%M', time.localtime(last))}, "
            f"{len(stamps) / days:.1f} entries/day"
        )
    return [
        ("WARN" if bad else "PASS", "shadow", text + (f"; {bad} unreadable lines" if bad else ""))
    ]
