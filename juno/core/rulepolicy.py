import re
from dataclasses import dataclass

from juno.core import topics, vaultfmt
from juno.core.errors import Refused
from juno.core.vaultfmt import RuleEntry, injected_rules, rule_entries

REPO_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
TICKET_SHAPE_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*-[0-9]+")


@dataclass(frozen=True)
class Scope:
    name: str
    relpath: str
    cap_key: str


def parse_scope(scope: str) -> Scope:
    if scope == "global":
        return Scope("global", "rules/global.md", "global_rule_lines")
    kind, _, name = scope.partition(":")
    if kind == "repo" and name.casefold().removesuffix(".md") == "global":
        raise Refused("repo name 'global' collides with the global rules file")
    if kind == "repo" and TICKET_SHAPE_RE.fullmatch(name):
        raise Refused(f"repo name {name} is ticket-shaped; ticket rules are retired")
    if kind == "repo" and REPO_NAME_RE.fullmatch(name):
        return Scope(scope, f"rules/{topics.REPO_DIR}/{name}.md", "scoped_rule_lines")
    if kind == "topic" and topics.SLUG_RE.match(name):
        return Scope(scope, topics.topic_relpath(name), "scoped_rule_lines")
    if kind == "topic":
        raise Refused("topic slug must match ^[a-z0-9][a-z0-9-]{1,39}$")
    if kind == "ticket":
        raise Refused(
            "ticket rules are retired; use --scope repo:<repo> or --scope topic:<slug> "
            "(existing rules/<KEY>.md files still load until moved with `juno rule move`)"
        )
    raise Refused("scope must be global, repo:<repo> or topic:<slug>")


def scope_of_path(relpath: str) -> str | None:
    return vaultfmt.rule_scope(relpath)


def clean_triggers(raw: str, preset: str) -> list[str]:
    found = topics.split_triggers(raw, preset)
    if not found:
        raise Refused("triggers are empty")
    for trigger in found:
        problem = topics.trigger_problem(trigger)
        if problem:
            raise Refused(problem)
    return found


def topic_triggers(scope: str, text: str, raw: str | None, preset: str) -> list[str] | None:
    if raw is not None and not scope.startswith("topic:"):
        raise Refused("--triggers is only for topic:<slug> scopes")
    if not scope.startswith("topic:"):
        return None
    current = topics.file_triggers(text, preset)
    if raw is None:
        if not current:
            raise Refused(
                f'topic {scope[len("topic:") :]} is new; give its triggers with --triggers "a,b,c"'
            )
        return current
    merged = current + [t for t in clean_triggers(raw, preset) if t not in current]
    if len(merged) > topics.MAX_TRIGGERS:
        raise Refused(
            f"topic {scope} would have {len(merged)} triggers (cap {topics.MAX_TRIGGERS})"
        )
    return merged


def check_caps(
    scope: str, relpath: str, text: str, line_cap: int, global_chars: int, rule: str
) -> bool:
    entries = rule_entries(text)
    if any(e.text == rule for e in entries):
        return True
    if len(entries) >= line_cap:
        raise Refused(
            f"{relpath} already has {len(entries)} rule lines (cap {line_cap}); remove one first"
        )
    if scope == "global":
        size = len(injected_rules(text)) + len(rule) + 3
        if size > global_chars:
            raise Refused(f"global rules would be {size} chars (cap {global_chars})")
    return False


def rule_line(date: str, rule: str, pid: str | None, quote: str | None) -> str:
    if pid is None or quote is None:
        return f"- {date} · {rule} · src: unquoted"
    return f'- {date} · {rule} · src: user {pid[:8]} · "{quote.replace(chr(34), chr(39))}"'


def append_line(text: str, scope: str, line: str, triggers: list[str] | None) -> str:
    body = text or f"# Rules: {scope}\n\n"
    if triggers:
        body = topics.set_triggers(body, scope, triggers)
    if not body.endswith("\n"):
        body += "\n"
    return body + line + "\n"


def remove_line(text: str, number: int) -> str:
    lines = text.splitlines()
    del lines[number - 1]
    return "\n".join(lines) + "\n"


def entry_at(text: str, relpath: str, index: int) -> RuleEntry:
    entries = rule_entries(text)
    if not 1 <= index <= len(entries):
        raise Refused(f"{relpath} has no rule number {index}")
    return entries[index - 1]


def has_rules(text: str) -> bool:
    return any(line.startswith("- ") for line in text.splitlines())
