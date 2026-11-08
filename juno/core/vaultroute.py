HINTS = {
    "handoff": "juno card",
    "knowledge": "juno knowledge add",
    "note": "juno note",
    "decision": "juno decision",
    "project": "juno project",
    "procedure": "juno procedure",
    "pitfalls": "juno pitfall",
    "threads": "juno thread add",
    "idea": "juno idea",
    "inbox": "juno capture",
    "self": "juno self",
    "rule": "juno rule",
    "backlog": "juno backlog",
    "task": "juno tasks (read only)",
}


def hint_for(kind: str) -> str:
    return HINTS.get(kind, "a juno verb (see core.md)")
