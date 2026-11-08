import re

from juno.core.errors import Refused
from juno.core.redact import find_secrets

MAX_LINES = 30
KEY_RE = re.compile(r"^  ([a-z][a-z0-9_]*): (\S.*)$")
Block = list[str]
SKELETON = "## Purpose\n\n## How to run\n\n## Gotchas\n\n## Links\n"


def parse(text: str) -> Block:
    lines = [x.rstrip() for x in text.strip("\n").split("\n")]
    if not lines or lines[0] != "runtime:" or len(lines) < 2:
        raise Refused(
            "the block must start with `runtime:` and hold at least one `  key: value` line"
        )
    if len(lines) > MAX_LINES + 1:
        raise Refused(f"the runtime block is over {MAX_LINES} lines")
    seen: set[str] = set()
    for line in lines[1:]:
        match = KEY_RE.match(line)
        if not match or match.group(1) in seen:
            raise Refused(f"runtime line {line!r} is not a unique `  key: value` line")
        seen.add(match.group(1))
        if find_secrets(line):
            raise Refused("the runtime block looks like it holds a secret")
    return lines


def apply(text: str, block: Block, repo: str, day: str) -> str:
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if not text or lines[0].rstrip() != "---" or end is None:
        head = ["---", f"repo: {repo}", f"updated: {day}", *block, "---", ""]
        return "\n".join(head) + "\n# " + repo + "\n\n" + SKELETON
    start = next((i for i in range(1, end) if lines[i].startswith("runtime:")), None)
    if start is None:
        return "\n".join([*lines[:end], *block, *lines[end:]])
    stop = start + 1
    while stop < end and (lines[stop][:1] in (" ", "\t") or not lines[stop].strip()):
        stop += 1
    while stop > start + 1 and not lines[stop - 1].strip():
        stop -= 1
    return "\n".join([*lines[:start], *block, *lines[stop:]])
