"""A `notes/*.md` file with a frontmatter and no body line is a leftover of `juno forget`."""

from collections.abc import Mapping

from juno.core.doctor_vault import Check

MIGRATE_HINT = "juno migrate --notes"


def split(text: str) -> tuple[list[str], list[str]]:
    lines = text.splitlines()
    if lines and lines[0].rstrip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
        if end is not None:
            return lines[1:end], lines[end + 1 :]
    return [], lines


def bodyless(text: str) -> bool:
    return not any(line.strip() for line in split(text)[1])


def front(text: str) -> str:
    return "; ".join(line.strip() for line in split(text)[0] if line.strip())


def archive_line(gone: str, verb: str, day: str, rel: str, wid: str | None, text: str) -> str:
    parts = [gone, f"{verb} {day}", f"from {rel}"]
    if wid:
        parts.append(wid)
    parts.append(f"note frontmatter: {front(text) or '-'}")
    return " · ".join(parts)


def body_checks(notes: Mapping[str, str]) -> list[Check]:
    empty = [n for n, text in sorted(notes.items()) if bodyless(text)]
    if not empty:
        return [("PASS", "note bodies", "every note has a body line")]
    return [
        ("WARN", "note bodies", f"notes/{n} has no body line; `{MIGRATE_HINT}` archives it")
        for n in empty
    ]
