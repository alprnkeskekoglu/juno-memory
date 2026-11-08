import datetime
import json
import os
import time
from typing import Any

from juno.core import isoweek, topics

PREREG: dict[str, Any] = {
    "hypothesis": "h",
    "assignment": {"fraction": 0.5},
    "unit": "a segment",
    "metric": {"primary": "segments per arm", "code": "segments"},
    "min_n": 40,
    "max_days": 30,
    "stop_rule": "decide at min_n or max_days",
    "decision": "on if on beats off",
}
PITFALL = "Do not run the formatter on generated files"
KNOWLEDGE = (
    "---\ncreated: 2026-01-01\nupdated: 2026-01-01\n---\n# {title}\n\n## Export\n\n{line}\n"
    + "The batch size controls how long one export run takes for a partner.\n" * 4
)
LINE = "- The export job times out on partners with more than ten thousand rows"


def put(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def jsonl(path: str, rows: list[dict[str, Any]]) -> None:
    put(path, "".join(json.dumps(r) + "\n" for r in rows))


def seed(
    root_state: str, vault: str, label: str, projects: str | None = None, claim: bool = True
) -> float:
    start, _ = isoweek.bounds(label)
    t = start + 2 * 86400 + 12 * 3600
    seg = "seg-aaaa-0001"
    prompts = ["always run the tests first", "look at the export job", "never touch core.md", "ok"]
    shadow = [
        {
            "ts": int(t + i),
            "segment": seg,
            "prompt": text,
            "suggestions": [{"path": "knowledge/a.md", "score": 1.0}],
            "delivered": [],
            "carried": [],
            "pasted": [],
            "alt": ["knowledge/b.md"],
        }
        for i, text in enumerate(prompts)
    ]
    jsonl(os.path.join(root_state, "shadow.jsonl"), shadow)
    ask = {"sid": seg, "tid8": "toolu_01", "q": 0, "n_options": 3, "multi": False, "other": False}
    jsonl(
        os.path.join(root_state, "asks.jsonl"),
        [
            {**ask, "t": t, "rec": 1, "picked": [1]},
            {**ask, "t": t + 1, "rec": 1, "picked": [1]},
            {**ask, "t": t + 2, "rec": 1, "picked": [0]},
        ],
    )
    sections = {"keys": "ok", "pitfalls": "dropped:invalid", "knowledge": "none"}
    runs = [
        {"t": t, "sid": seg, "status": "ok", "cost_usd": 0.05, "sections": sections},
        {
            "t": t + 86400,
            "sid": "seg-bbbb-0002",
            "status": "ok",
            "cost_usd": 0.03,
            "sections": {**sections, "pitfalls": "ok"},
            "turns": {"proposals": 20, "pushback": 3},
        },
    ]
    jsonl(os.path.join(root_state, "cards", "runs.jsonl"), runs)
    jsonl(
        os.path.join(root_state, "writes.jsonl"),
        [
            {"id": "w-00000001", "t": t, "verb": "note", "sid": seg},
            {"id": "w-00000002", "t": t + 1, "verb": "note", "sid": seg},
            {"id": "w-00000003", "t": t + 2, "verb": "undo", "of": "w-00000001", "sid": seg},
        ],
    )
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
    put(
        os.path.join(root_state, "hook-errors.log"),
        f"{stamp} PreToolUse KeyError x.py:1 'boom'\n{stamp} PreToolUse OSError y.py:2 'disk'\n",
    )
    put(
        os.path.join(vault, "pitfalls.md"),
        f"# Pitfalls\n\n- {PITFALL} · triggers: formatter · evidence · 2026-10-01\n",
    )
    marker = topics.pitfall_name(PITFALL)
    if claim:
        for sid in ("sidone", "sidtwo"):
            path = os.path.join(root_state, "topics", f"{sid}.main.{marker}")
            put(path, "")
            os.utime(path, (t, t))
    put(
        os.path.join(vault, "inbox.md"),
        "# Inbox\n\n- loose item to sort later · aaaa · 2026-10-01\n",
    )
    for name in ("a", "b"):
        put(os.path.join(vault, "knowledge", f"{name}.md"), KNOWLEDGE.format(title=name, line=LINE))
    jsonl(
        os.path.join(root_state, "usage.jsonl"),
        [{"t": 1.0, "sid8": "seed0000", "rel": "x.md", "ev": "read"}],
    )
    arm = {"name": "segs", "salt": "ab", "status": "running", "started": int(t), "prereg": PREREG}
    put(os.path.join(root_state, "arms", "segs.json"), json.dumps(arm))
    if projects:
        when = datetime.datetime.fromtimestamp(t + 0.5, datetime.UTC).isoformat()
        read = {
            "type": "tool_use",
            "name": "Read",
            "input": {"file_path": f"{vault}/knowledge/a.md"},
        }
        line = {"type": "assistant", "timestamp": when, "message": {"content": [read]}}
        put(os.path.join(projects, "-p", f"{seg}.jsonl"), json.dumps(line) + "\n")
    return t
