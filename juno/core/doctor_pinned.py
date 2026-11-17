from collections.abc import Mapping

from juno.core import pinned
from juno.core.vaultfmt import rule_entries, rule_scope

Check = tuple[str, str, str]


def pinned_checks(files: Mapping[str, str], cap: int) -> list[Check]:
    """WARN when the pinned rules, as the prompt hook renders them, are longer than `cap`."""
    found: list[tuple[str, int, str]] = []
    for name, text in sorted(files.items(), key=lambda kv: (kv[0] != "global.md", kv[0])):
        scope = rule_scope(f"rules/{name}") or name
        found += [
            (scope, i, e.text) for i, e in enumerate(rule_entries(text), 1) if e.pinned and e.text
        ]
    if not found:
        return []
    block = pinned.render([t for _, _, t in found])
    if len(block) <= cap:
        return [("PASS", "pinned rules", f"{len(found)} pinned, {len(block)} of {cap} chars")]
    names = ", ".join(f"{scope} #{i}" for scope, i, _ in found)
    return [
        (
            "WARN",
            "pinned rules",
            f"pinned rules are {len(block)} chars (pinned_rules_chars {cap}); the hook cuts "
            f"the tail: {names}",
        )
    ]
