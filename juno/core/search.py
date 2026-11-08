import datetime
import difflib
import functools
import re
import unicodedata
from collections import Counter
from typing import Any

from juno.core import vaultfmt

STRIDE = 10000
PASSAGE_TARGET = 900
MAX_PASSAGES_PER_FILE = 200
SNIPPET_MAX = 300
MAX_HEADING_LINE = 500
MAX_QUERY_TOKENS = 32
MAX_TOKEN_LEN = 64
CORRECT_MIN_LEN = 4
CORRECT_CUTOFF = 0.75
CORRECT_MAX = 3
TICKET_SCORE = 100.0
STUB_LIKE = "%· stub ·%"
KEYS_WEIGHT = 2.0
TR_I = str.maketrans({"\u0131": "i", "\u0130": "I"})

KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*-[0-9]+")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
TOKEN_RE = re.compile(r"[^\W_]+")
URL_RE = re.compile(r"(?:\b[a-z][a-z0-9+.-]*://|\bwww\.)\S+", re.IGNORECASE)
TICKET_STEM_RE = re.compile(r"[A-Z][A-Z0-9]*-[0-9]+")
QUERY_KEY_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*-[0-9]+(?![0-9])")
REPO_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SPLIT_RE = re.compile(r"[,\s]+")
ISO_DAY_RE = re.compile(r"(?<![0-9])[0-9]{4}-[0-9]{2}-[0-9]{2}(?![0-9])")
DAY_FIELDS = ("updated", "valid_from", "created")

Passage = tuple[str, str]
Meta = tuple[str, bool]
Row = tuple[str, str, str, float, str, bool]
Candidate = tuple[str, str, str, object, float, Meta]


def bm25_weights(keys_weight: float) -> tuple[float, ...]:
    return (6.0, 4.0, 1.0, max(0.0, keys_weight), 0.0, 0.0, 0.0, 0.0)


def fold_text(text: str, preset: str = "none") -> str:
    return text.translate(TR_I) if preset == "tr" else text


@functools.lru_cache(maxsize=4096)
def fold_char(ch: str, tr: bool) -> str:
    ch = ch.translate(TR_I) if tr else ch
    base = "".join(c for c in unicodedata.normalize("NFD", ch) if not unicodedata.combining(c))
    return base.lower() if len(base) == 1 else ch


def fold_same_length(text: str, preset: str = "none") -> str:
    tr = preset == "tr"
    return "".join(fold_char(c, tr) for c in text)


def heading(line: str) -> tuple[int, str] | None:
    if len(line) > MAX_HEADING_LINE:
        return None
    match = HEADING_RE.match(line)
    if not match:
        return None
    text = match.group(2).rstrip().rstrip("#").rstrip()
    return (len(match.group(1)), text) if text else None


def chunk(text: str, limit: int) -> list[str]:
    pieces: list[str] = []
    buf = ""
    for para in re.split(r"\n[ \t]*\n", text):
        para = para.strip()
        if not para:
            continue
        while len(para) > limit:
            cut = para.rfind(" ", limit * 2 // 3, limit)
            cut = cut if cut > 0 else limit
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.append(para[:cut].strip())
            para = para[cut:].strip()
        if not para:
            continue
        if buf and len(buf) + 2 + len(para) > limit:
            pieces.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        pieces.append(buf)
    return pieces


def split_passages(body: str, limit: int = PASSAGE_TARGET) -> list[Passage]:
    sections: list[Passage] = []
    stack: list[tuple[int, str]] = []
    current: list[str] = []
    path = ""
    in_fence = False
    for line in body.split("\n"):
        if FENCE_RE.match(line):
            in_fence = not in_fence
        found = None if in_fence else heading(line)
        if found:
            sections.append((path, "\n".join(current)))
            stack = [(lv, t) for lv, t in stack if lv < found[0]]
            stack.append(found)
            path = " > ".join(t for _, t in stack)
            current = []
        else:
            current.append(line)
    sections.append((path, "\n".join(current)))
    out: list[Passage] = []
    for head, text in sections:
        text = text.strip()
        if not text:
            continue
        for piece in chunk(text, limit):
            out.append((head, piece))
            if len(out) >= MAX_PASSAGES_PER_FILE:
                return out
    return out


def split_list(value: str) -> list[str]:
    return [t.strip() for t in SPLIT_RE.split(value) if t.strip()]


def is_repo_name(tok: str) -> bool:
    return bool(REPO_NAME_RE.fullmatch(tok)) and tok.lower() not in ("none", "-")


def flat_values(fields: vaultfmt.Fields, name: str) -> list[str]:
    return [tok for item in vaultfmt.field_items(fields, name) for tok in split_list(item)]


def extract_meta(relpath: str, fields: vaultfmt.Fields, body: str) -> tuple[list[str], list[str]]:
    tickets: set[str] = set()
    repos: set[str] = set()
    parts = relpath.split("/")
    stem = parts[-1][:-3] if parts[-1].endswith(".md") else parts[-1]
    if len(parts) == 3 and parts[:2] == ["rules", "repos"]:
        repos.add(stem)
    if len(parts) == 2:
        is_key = bool(KEY_RE.fullmatch(stem))
        if parts[0] in ("tasks", "handoffs") and is_key:
            tickets.add(stem.upper())
        elif parts[0] == "projects":
            repos.add(stem)
        elif parts[0] == "rules":
            if is_key:
                tickets.add(stem.upper())
            elif stem != "global":
                repos.add(stem)
    for name in ("jira", "tickets", "ticket"):
        tickets.update(t.upper() for t in flat_values(fields, name) if KEY_RE.fullmatch(t))
    for name in ("repo", "repos"):
        repos.update(t for t in flat_values(fields, name) if is_repo_name(t))
    for line in body.split("\n"):
        if line.startswith("Tickets:"):
            tickets.update(t.upper() for t in split_list(line[8:]) if KEY_RE.fullmatch(t))
        elif line.startswith("Repos:"):
            repos.update(t for t in split_list(line[6:]) if is_repo_name(t))
    return sorted(tickets), sorted(repos)


def newest_day(text: str) -> str:
    found = []
    for raw in ISO_DAY_RE.findall(text):
        try:
            found.append(datetime.date.fromisoformat(raw).isoformat())
        except ValueError:
            continue
    return max(found, default="")


def file_day(relpath: str, fields: vaultfmt.Fields, mtime_ns: int) -> str:
    for name in DAY_FIELDS:
        value = fields.get(name)
        day = newest_day(value) if isinstance(value, str) else ""
        if day:
            return day
    if relpath.startswith("notes/"):
        day = newest_day(relpath.rsplit("/", 1)[-1])
        if day:
            return day
    return datetime.datetime.fromtimestamp(mtime_ns / 1e9).date().isoformat()


def passage_day(head: str, raw: str, fallback: str) -> str:
    return newest_day(f"{head}\n{raw}") or fallback


def is_superseded(relpath: str, head: str) -> bool:
    return relpath.startswith("decisions/") and head.rpartition(" > ")[2] == "History"


def title_of(relpath: str, fields: vaultfmt.Fields, body: str) -> str:
    value = fields.get("title")
    title = vaultfmt.unquote(value) if isinstance(value, str) and value else ""
    if not title:
        for line in body.split("\n"):
            found = heading(line)
            if found and found[0] == 1:
                title = found[1]
                break
    return title or relpath.rsplit("/", 1)[-1][:-3]


def query_tokens(query: object, preset: str = "none") -> list[str]:
    if not isinstance(query, str):
        return []
    tokens = TOKEN_RE.findall(fold_text(URL_RE.sub(" ", query), preset))
    return [t[:MAX_TOKEN_LEN] for t in tokens[:MAX_QUERY_TOKENS]]


def query_ticket_files(query: object) -> list[str]:
    if not isinstance(query, str):
        return []
    keys = dict.fromkeys(k.upper() for k in QUERY_KEY_RE.findall(query))
    return [f"{top}/{k}.md" for k in keys for top in ("tasks", "handoffs")]


def snippet(raw: str, tokens: list[str], preset: str = "none") -> str:
    text = re.sub(r"\s+", " ", raw).strip()
    if len(text) <= SNIPPET_MAX:
        return text
    folded = fold_same_length(text, preset)
    pos = -1
    for tok in tokens:
        idx = folded.find(fold_same_length(tok.lower(), preset))
        if idx >= 0 and (pos < 0 or idx < pos):
            pos = idx
    start = max(0, pos - 80) if pos >= 0 else 0
    if start:
        space = text.find(" ", start)
        start = space + 1 if 0 <= space < start + 20 else start
    prefix = "..." if start else ""
    room = SNIPPET_MAX - len(prefix)
    piece = text[start : start + room]
    if start + room < len(text):
        piece = piece[: room - 3].rstrip() + "..."
    return prefix + piece


def dup_key(rel: str, head: str, raw: str) -> object:
    body = " ".join(raw.split())
    return ((head or "").rpartition(" > ")[2], body) if rel.startswith("handoffs/") else body


def is_ticket_file(rel: str) -> bool:
    return TICKET_STEM_RE.fullmatch(rel.rsplit("/", 1)[-1][:-3]) is not None


def kind_of(rel: str) -> str:
    return rel.split("/", 1)[0]


def hit(
    rel: str, head: str, raw: str, tokens: list[str], score: float, preset: str, meta: Meta
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "relpath": rel,
        "heading": head,
        "snippet": snippet(raw, tokens, preset),
        "score": round(score, 6),
        "day": meta[0],
    }
    if meta[1]:
        out["superseded"] = True
    return out


def pick(
    out: list[dict[str, Any]],
    cands: list[Candidate],
    limit: int,
    cap: int,
    shown: list[str],
    preset: str,
) -> list[dict[str, Any]]:
    used = Counter(kind_of(h["relpath"]) for h in out)
    chosen: list[Candidate] = []
    rest: list[Candidate] = []
    for cand in cands:
        kind = kind_of(cand[0])
        if len(out) + len(chosen) < limit and used[kind] < cap:
            used[kind] += 1
            chosen.append(cand)
        else:
            rest.append(cand)
    chosen += rest[: limit - len(out) - len(chosen)]
    chosen.sort(key=lambda c: -c[4])
    out += [
        hit(rel, head, raw, shown, score, preset, meta) for rel, head, raw, _, score, meta in chosen
    ]
    return out


def one_edit(a: str, b: str) -> bool:
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return (
            len(diff) == 2
            and diff[1] == diff[0] + 1
            and a[diff[0]] == b[diff[1]]
            and a[diff[1]] == b[diff[0]]
        )
    if abs(len(a) - len(b)) != 1:
        return False
    short, long = sorted((a, b), key=len)
    return any(long[:i] + long[i + 1 :] == short for i in range(len(long)))


def close_words(word: str, terms: list[str]) -> list[str]:
    close = difflib.get_close_matches(word, terms, n=CORRECT_MAX, cutoff=CORRECT_CUTOFF)
    return [w for w in close if one_edit(word, w)]


def any_of(words: list[str]) -> str:
    quoted = " OR ".join(f'"{w}"' for w in words)
    return f"({quoted})" if len(words) > 1 else quoted


def match_modes(tokens: list[str], fixes: dict[str, list[str]]) -> list[str]:
    groups = [[t, *fixes.get(t, [])] for t in tokens]
    modes = [" AND ".join(any_of(g) for g in groups)]
    if len(groups) > 1:
        modes.append(" OR ".join(f'"{w}"' for g in groups for w in g))
    return modes


def dedupe(rows: list[Row], seen: set[str], ticket_keys: list[object]) -> list[Candidate]:
    cands: list[Candidate] = []
    keys: dict[object, int | None] = dict.fromkeys(ticket_keys)
    file_keys: dict[str, list[object]] = {}
    for rel, head, raw, _, _, _ in rows:
        file_keys.setdefault(rel, []).append(dup_key(rel, head, raw))
    for rel, head, raw, score, day, sup in rows:
        if rel in seen:
            continue
        key = dup_key(rel, head, raw)
        if key in keys:
            at = keys[key]
            if (
                at is not None
                and round(-score, 6) == round(cands[at][4], 6)
                and is_ticket_file(rel)
                and not is_ticket_file(cands[at][0])
            ):
                seen.add(rel)
                cands[at] = (rel, head, raw, key, cands[at][4], (day, bool(sup)))
            continue
        seen.add(rel)
        for other in file_keys[rel]:
            keys.setdefault(other, None)
        keys[key] = len(cands)
        cands.append((rel, head, raw, key, -score, (day, bool(sup))))
    return cands
