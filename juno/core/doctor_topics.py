"""Doctor checks for trigger matching (#36): files that are never found, items fired most."""

from collections.abc import Mapping

from juno.core import topics, vaultfmt

Check = tuple[str, str, str]


def untriggered(files: Mapping[str, str], preset: str = "none") -> list[Check]:
    """A knowledge file without triggers is never injected by a matching call."""
    out: list[Check] = []
    for name, text in sorted(files.items()):
        fields, _ = vaultfmt.parse_frontmatter(text)
        if vaultfmt.is_archived(fields) or vaultfmt.is_private(fields):
            continue
        if not topics.KNOWLEDGE_RE.match(name) or topics.field_list(fields.get("triggers"), preset):
            continue
        out.append(
            (
                "WARN",
                "knowledge",
                f"knowledge/{name} has no triggers: a matching call never injects it; the "
                "session-end run proposes some after a session reads it, `juno triggers propose` "
                "queues proposals (pending until you accept them) for every such file, or add a "
                "`triggers:` line to its front matter",
            )
        )
    return out


def most_fired(top: list[tuple[str, int]]) -> list[Check]:
    if not top:
        return []
    shown = ", ".join(f"{name} ({count})" for name, count in top)
    return [("INFO", "topics", f"most injected in the last 7 days: {shown}")]
