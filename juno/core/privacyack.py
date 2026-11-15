import json
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from juno.core import privacy
from juno.core.errors import Refused
from juno.core.redact import find_secrets

ACK_FILE = ".privacy-ack.jsonl"
Record = Mapping[str, Any]


def id_spans(line: str, prefixes: Sequence[str] | None = None) -> list[str]:
    return [line[a:b] for a, b, kind in privacy.personal_spans(line, prefixes) if kind == "id"]


def acknowledgeable(line: str, was: str, prefixes: Sequence[str] | None = None) -> str:
    """The id span of `line` that `was` names; refuses a line with any other kind of finding."""
    other = [
        *find_secrets(line),
        *sorted({k for _, _, k in privacy.personal_spans(line, prefixes) if k != "id"}),
    ]
    if other:
        raise Refused(
            f"the line also has a {', '.join(other)} finding; only `id` findings can be "
            "acknowledged, so reword or `juno forget` the line"
        )
    wanted = "".join(was.split())
    for span in id_spans(line, prefixes):
        if "".join(span.split()) == wanted:
            return span
    raise Refused("--was does not match an id finding on that line (digits only, as flagged)")


def without_acked(text: str, acked: Collection[str], prefixes: Sequence[str] | None = None) -> str:
    """`text` with each acknowledged id span replaced by a placeholder, for the doctor scan."""
    if not acked:
        return text
    out, pos = [], 0
    for a, b, kind in sorted(privacy.personal_spans(text, prefixes)):
        if kind == "id" and a >= pos and privacy.span_hash(text[a:b]) in acked:
            out += [text[pos:a], "<acked>"]
            pos = b
    return "".join(out) + text[pos:]


def record(file: str, span: str, line: int, evidence: str, when: str) -> dict[str, Any]:
    return {
        "file": file,
        "h": privacy.span_hash(span),
        "line": line,
        "t": when,
        "evidence": evidence,
    }


def parse(text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for raw in text.splitlines():
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict) and isinstance(rec.get("file"), str) and rec.get("h"):
            found.append(rec)
    return found


def add(text: str, rec: Record) -> str:
    """The ignore file with `rec` appended; unchanged when that file and span are recorded."""
    if any(r["file"] == rec["file"] and r["h"] == rec["h"] for r in parse(text)):
        return text
    body = text if not text or text.endswith("\n") else text + "\n"
    return body + json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n"


def hashes(records: Sequence[Record], file: str | None = None) -> frozenset[str]:
    """Fingerprints of one file, or of every file when `file` is None."""
    return frozenset(str(r["h"]) for r in records if file is None or r["file"] == file)


def resolve(text: str, rec: Record, prefixes: Sequence[str] | None = None) -> int | None:
    """Current line number of the acknowledged span in `text`, or None when it is gone."""
    lines = text.splitlines()
    order: list[int] = [int(rec.get("line") or 0) - 1, *range(len(lines))]
    for index in order:
        if 0 <= index < len(lines) and any(
            privacy.span_hash(s) == rec["h"] for s in id_spans(lines[index], prefixes)
        ):
            return index + 1
    return None


def count_line(records: Collection[Record]) -> str:
    n = len(records)
    return f"{n} acknowledged id finding{'s' if n != 1 else ''} (juno privacy list)"
