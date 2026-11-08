import json
import re
from dataclasses import dataclass, field
from typing import Any

from juno.core import fold, privacy, topics, vaultfmt
from juno.core.redact import TICKET_SHAPE_RE
from juno.core.textchecks import SECOND_PERSON_RE, anchored, as_list, text_field

MARKER = "---JUNO-EXTRACT---"
MARKER_RE = re.compile(r"^[ \t]*-{3}JUNO-EXTRACT-{3}[ \t]*$", re.M)
PROFILE_RE = re.compile(r"^projects/([A-Za-z0-9][A-Za-z0-9._-]{0,79})\.md$")
KNOWLEDGE_RE = re.compile(r"^knowledge/([a-z0-9][a-z0-9-]{2,59})\.md$")
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
QUOTE_MAX = 300
RULES_CONSIDERED = 6
RULES_PER_RUN = 3
FACTS_CONSIDERED = 6
FACTS_PER_RUN = 3
FACTS_PER_FILE = 2
FACT_CHARS = (20, 300)
EVIDENCE_CHARS = (4, 200)
TITLE_CHARS = (5, 80)
PITFALLS_CONSIDERED = 4
PITFALLS_PER_RUN = 2
PITFALL_TRIGGERS = 5
NEW_TRIGGERS = 5
THREAD_OPENS = 1
THREAD_CLOSES = 2
THREAD_CHARS = (10, 160)
THREAD_LINES = 30
THREAD_TAG_LINES = 5
RULE_COUNTS = ("proposed", "queued", "unverified", "duplicate", "invalid", "capped")


@dataclass
class RuleContext:
    sources: list[tuple[str, str]]
    repos: list[str]
    rule_files: set[str] = field(default_factory=set)
    topic_triggers: dict[str, list[str]] = field(default_factory=dict)
    preset: str = "none"
    rule_max: int = 300
    quote_min: int = 20


def split_extract(text: str) -> tuple[str, str | None]:
    match = MARKER_RE.search(text or "")
    if not match:
        return text, None
    return text[: match.start()], text[match.end() :]


def parse_extract(raw: str | None) -> dict[str, Any]:
    if raw is None:
        raise ValueError("no extract block")
    lines = raw.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    try:
        data = json.loads("\n".join(lines))
    except ValueError:
        raise ValueError("extract block is not JSON") from None
    if not isinstance(data, dict):
        raise ValueError("extract block is not a JSON object")
    return data


def quote_key(quote: str, preset: str = "none") -> str:
    return fold.fold(quote.replace('"', "'"), preset)


def clean_triggers(raw: object, preset: str = "none") -> list[str]:
    values = [v for v in as_list(raw) if isinstance(v, str)]
    found = topics.split_triggers(",".join(values), preset)
    return [t for t in found if not topics.trigger_problem(t)][:NEW_TRIGGERS]


def check_scope(ctx: RuleContext, scope: str, triggers: object) -> tuple[str, list[str]] | None:
    if scope == "global":
        return scope, []
    kind, _, name = scope.partition(":")
    if kind == "repo":
        known = {r.casefold(): r for r in ctx.repos}
        name = known.get(name.casefold(), name)
        if name.casefold() not in known and name not in ctx.rule_files:
            return None
        bad = name == "global" or TICKET_SHAPE_RE.fullmatch(name)
        if bad or not REPO_NAME_RE.match(name):
            return None
        return f"repo:{name}", []
    if kind != "topic" or not topics.SLUG_RE.match(name):
        return None
    if ctx.topic_triggers.get(name):
        return f"topic:{name}", []
    picked = clean_triggers(triggers, ctx.preset)
    return (f"topic:{name}", picked) if picked else None


def verified_pid(ctx: RuleContext, quote: str) -> str | None:
    folded = fold.fold(quote, ctx.preset)
    for pid, text in ctx.sources:
        if folded and folded in fold.fold(text, ctx.preset):
            return pid
    return None


def check_rule(ctx: RuleContext, item: object) -> tuple[str, dict[str, Any] | None]:
    if not isinstance(item, dict):
        return "invalid", None
    rule, quote = text_field(item.get("rule")), text_field(item.get("quote"))
    if not 10 <= len(rule) <= ctx.rule_max or "·" in rule or privacy.scan(rule):
        return "invalid", None
    if not ctx.quote_min <= len(quote) <= QUOTE_MAX or "·" in quote or privacy.scan(quote):
        return "invalid", None
    scoped = check_scope(ctx, text_field(item.get("scope")), item.get("triggers"))
    if scoped is None:
        return "invalid", None
    pid = verified_pid(ctx, quote)
    if pid is None:
        return "unverified", None
    value = {
        "rule": rule,
        "quote": quote,
        "scope": scoped[0],
        "triggers": scoped[1],
        "pid": pid[:8],
    }
    return "ok", value


def known_rule_keys(
    rule_texts: list[str], quotes: list[str], preset: str
) -> tuple[set[str], set[str]]:
    return (
        {fold.fold(t, preset) for t in rule_texts},
        {quote_key(q, preset) for q in quotes},
    )


def rules_in(text: str) -> tuple[list[str], list[str]]:
    texts: list[str] = []
    quotes: list[str] = []
    for entry in vaultfmt.rule_entries(text):
        if entry.text:
            texts.append(entry.text)
        if entry.quote:
            quotes.append(entry.quote)
    return texts, quotes


def check_fact(
    item: object, digest: str, repos: list[str]
) -> tuple[str | None, str, dict[str, str]]:
    if not isinstance(item, dict):
        return "invalid", "", {}
    rel = text_field(item.get("file"))
    fact, evidence = text_field(item.get("text")), text_field(item.get("evidence"))
    if not FACT_CHARS[0] <= len(fact) <= FACT_CHARS[1] or SECOND_PERSON_RE.search(fact):
        return "invalid", rel, {}
    if not EVIDENCE_CHARS[0] <= len(evidence) <= EVIDENCE_CHARS[1]:
        return "no evidence", rel, {}
    if privacy.scan(fact) or privacy.scan(evidence):
        return "private", rel, {}
    if not anchored(evidence, digest):
        return "unanchored", rel, {}
    profile = PROFILE_RE.match(rel)
    if profile and profile.group(1).casefold() in {r.casefold() for r in repos}:
        return None, rel, {"text": fact, "evidence": evidence}
    if KNOWLEDGE_RE.match(rel):
        return None, rel, {"text": fact, "evidence": evidence}
    return "path", rel, {}


def pitfall_item(item: object, digest: str) -> tuple[str | None, dict[str, Any] | None]:
    if not isinstance(item, dict):
        return "invalid", None
    text, evidence = text_field(item.get("text")), text_field(item.get("evidence"))
    bad_text = "·" in text or "[[" in text or SECOND_PERSON_RE.search(text)
    if not FACT_CHARS[0] <= len(text) <= FACT_CHARS[1] or bad_text:
        return "invalid", None
    if not EVIDENCE_CHARS[0] <= len(evidence) <= EVIDENCE_CHARS[1] or "·" in evidence:
        return "no evidence", None
    if privacy.scan(text) or privacy.scan(evidence):
        return "private", None
    if not anchored(evidence, digest):
        return "unanchored", None
    raw = item.get("triggers")
    if raw is not None and not (isinstance(raw, list) and all(isinstance(v, str) for v in raw)):
        return "trigger", None
    triggers = topics.split_triggers(",".join(str(v) for v in as_list(raw)))
    if len(triggers) > PITFALL_TRIGGERS or any(topics.trigger_problem(t) for t in triggers):
        return "trigger", None
    return None, {"text": text, "evidence": evidence, "triggers": triggers}


def thread_tags(line: str) -> list[str]:
    match = vaultfmt.THREAD_RE.match(line.rstrip())
    return [t.strip().casefold() for t in match.group(1).split(",")] if match else []


def related(line: str, tags: dict[str, str] | set[str]) -> bool:
    if any(t in tags for t in thread_tags(line)):
        return True
    match = vaultfmt.THREAD_RE.match(line.rstrip())
    body = match.group(2).casefold() if match else ""
    return any(re.search(r"(?<![\w-])" + re.escape(t) + r"(?![\w-])", body) for t in tags)


def segment_tags(repos: list[str], tickets: list[str]) -> dict[str, str]:
    return {t.casefold(): t for t in [*repos, *tickets]}
