import re
from dataclasses import dataclass

from juno.core import fold
from juno.core.errors import Refused

RUN_MAX = 50
LINE_MIN = 8
LINE_MAX = 300
NEAR_RATIO = 0.88
TEXT_MAX = {"note": 2000, "idea": 1500, "card": 1500, "procedure": 2000}
FORGET_DENIED = ("rules", "protocol", "_generated", "archive", ".git")
FIX_DENIED_FILES = ("core.md", "juno-brain.toml")
REF_RE = re.compile(r"^(?P<rel>[^:\n]+\.md):(?P<line>[1-9][0-9]{0,5})$")
WRITE_ID_RE = re.compile(r"^w-[0-9a-f]{8}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,59}$")


@dataclass(frozen=True)
class Hunk:
    start: int
    old: list[str]
    new: list[str]


def check_slug(value: str, label: str = "slug") -> str:
    if not SLUG_RE.match(value):
        raise Refused(f"{label} must match ^[a-z0-9][a-z0-9-]{{1,59}}$")
    return value


def check_length(text: str, kind: str) -> None:
    if len(text) > TEXT_MAX[kind]:
        raise Refused(f"{kind} text is longer than {TEXT_MAX[kind]} characters")


def check_line(text: str, label: str) -> None:
    if not LINE_MIN <= len(text) <= LINE_MAX:
        raise Refused(f"{label} must be {LINE_MIN}-{LINE_MAX} characters")


def duplicate(lines: list[str], text: str, preset: str) -> tuple[int, bool] | None:
    import difflib

    wanted = fold.fold(text, preset)
    best: tuple[int, bool] | None = None
    top = 0.0
    for index, line in enumerate(lines):
        have = fold.fold(line, preset)
        if have == wanted:
            return index, True
        ratio = difflib.SequenceMatcher(None, have, wanted, autojunk=False).ratio()
        if ratio >= NEAR_RATIO and ratio > top:
            best, top = (index, False), ratio
    return best


def hunks(old: str, new: str) -> list[Hunk]:
    import difflib

    a, b = old.splitlines(), new.splitlines()
    found: list[Hunk] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            found.append(Hunk(i1, a[i1:i2], b[j1:j2]))
    return found


def encode(items: list[Hunk]) -> list[list[object]]:
    return [[h.start, h.old, h.new] for h in items]


def decode(raw: object) -> list[Hunk]:
    found: list[Hunk] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, list) and len(item) == 3:
            found.append(Hunk(int(item[0]), [str(x) for x in item[1]], [str(x) for x in item[2]]))
    return found


def touched(items: list[Hunk]) -> set[str]:
    return {line for h in items for line in (*h.old, *h.new) if line.strip()}


def revert(current: str, items: list[Hunk]) -> str | None:
    lines = current.splitlines()
    for h in sorted(items, key=lambda x: -x.start):
        if h.new:
            at = find_run(lines, h.new, h.start)
            if at is None:
                return None
            lines[at : at + len(h.new)] = h.old
        else:
            lines[min(h.start, len(lines)) : min(h.start, len(lines))] = h.old
    return "\n".join(lines) + "\n" if lines else ""


def find_run(lines: list[str], block: list[str], near: int) -> int | None:
    spots = [i for i in range(len(lines) - len(block) + 1) if lines[i : i + len(block)] == block]
    return min(spots, key=lambda i: abs(i - near)) if spots else None


def conflicts(mine: list[Hunk], created: bool, later: list[list[Hunk]]) -> bool:
    if created:
        return bool(later)
    lines = {line for h in mine for line in h.new if line.strip()}
    if not lines:
        lines = touched(mine)
    return any(lines & touched(other) for other in later)


def split_ref(ref: str) -> tuple[str, int]:
    match = REF_RE.match(ref.strip())
    if not match:
        raise Refused("ref must look like <vault-relative file>.md:<line number>")
    rel = match["rel"].replace("\\", "/").lstrip("/")
    if ".." in rel.split("/"):
        raise Refused("ref must stay inside the vault")
    return rel, int(match["line"])


def check_target(rel: str, forget: bool) -> None:
    top = rel.split("/", 1)[0]
    if top in FORGET_DENIED or rel in FIX_DENIED_FILES:
        raise Refused(f"{rel} is not corrected through fix/forget (rules use `juno rule`)")
    if forget and rel == "threads.md":
        raise Refused(
            "close threads with `juno thread close` (--line N --was TEXT --evidence YOUR_WORDS)"
        )


def check_line_fix(line: str) -> None:
    stripped = line.strip()
    if not stripped:
        raise Refused("that line is blank")
    if stripped.startswith(("#", "---", "<!--")):
        raise Refused("only content lines can be fixed or forgotten, not headings or markers")


def core_text(line: str) -> str:
    first = (line[2:] if line.startswith("- ") else line).split(" · ")[0]
    return re.sub(r"\s*\([^()]*\)$", "", first).strip()


def join(lines: list[str]) -> str:
    return "\n".join(lines).rstrip("\n") + "\n"


def add_item(
    text: str, line: str, preset: str, section: str | None = None, base: str = ""
) -> tuple[str, str]:
    lines = (text or base).splitlines()
    start, end = 0, len(lines)
    if section is not None:
        head = f"## {section}"
        at = next((i for i, x in enumerate(lines) if x.strip() == head), None)
        if at is None:
            while lines and not lines[-1].strip():
                lines.pop()
            lines += ["", head, ""]
            at = len(lines) - 2
        start = at + 1
        end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    items = [i for i in range(start, end) if lines[i].startswith("- ")]
    found = duplicate([core_text(lines[i]) for i in items], core_text(line), preset)
    if found:
        index, exact = found
        if exact:
            return text, "same"
        lines[items[index]] = line
        return join(lines), "updated"
    if items:
        lines.insert(items[-1] + 1, line)
    else:
        spot = start if section is not None else end
        while section is None and spot > 0 and not lines[spot - 1].strip():
            spot -= 1
        block = ["", line] if section is not None else [line]
        if section is not None and spot < len(lines) and lines[spot].strip():
            block.append("")
        lines[spot:spot] = block
    return join(lines), "added"


def frontmatter(fields: dict[str, str]) -> str:
    return "---\n" + "".join(f"{k}: {v}\n" for k, v in fields.items()) + "---\n"


def set_field(text: str, name: str, value: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return text
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if end is None:
        return text
    for i in range(1, end):
        if lines[i].startswith(name + ":"):
            lines[i] = f"{name}: {value}"
            return join(lines)
    lines.insert(end, f"{name}: {value}")
    return join(lines)
