import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from juno.core import fold, prompts
from juno.core.detect import Tickets

MAX_LINE_BYTES = 4000
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SHRINKABLE = ("repos", "edits", "tickets", "cmd_tickets", "keys")

Record = dict[str, Any]


@dataclass(frozen=True)
class Rules:
    meaningful_tool_calls: int = 15
    meaningful_user_turns: int = 5
    dirty_user_turns: int = 10
    dirty_tool_calls: int = 40
    preset: str = "none"
    tickets: Tickets = field(default_factory=Tickets)


def safe_id(sid: object) -> bool:
    return isinstance(sid, str) and SAFE_ID.match(sid) is not None


def encode(record: Record) -> bytes:
    return (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def shrink(record: Record) -> tuple[Record, bytes]:
    record = dict(record)
    lists = [k for k in SHRINKABLE if isinstance(record.get(k), list)]
    dropped = 0
    while lists:
        data = encode(record)
        if len(data) <= MAX_LINE_BYTES:
            return record, data
        key = max(lists, key=lambda k: len(json.dumps(record[k], ensure_ascii=False)))
        if not record[key]:
            lists.remove(key)
            continue
        record[key] = record[key][:-1]
        dropped += 1
        record["trunc"] = dropped
    data = encode(record)
    if len(data) > MAX_LINE_BYTES:
        raise ValueError("ledger line over limit")
    return record, data


def encode_capped(record: Record) -> tuple[Record, bytes, bool]:
    data = encode(record)
    if len(data) <= MAX_LINE_BYTES:
        return record, data, False
    shrunk, data = shrink(record)
    return shrunk, data, True


class Segment:
    def __init__(self, sid: str, rules: Rules | None = None) -> None:
        self.sid = sid
        self.rules = rules or Rules()
        self.turns = self.tools = self.since_turns = self.since_tools = 0
        self.fails = 0
        self.edits: Counter[str] = Counter()
        self.ticket_edits: Counter[str] = Counter()
        self.tickets: Counter[str] = Counter()
        self.typed_tickets: Counter[str] = Counter()
        self.start_tickets: Counter[str] = Counter()
        self.main_tickets: Counter[str] = Counter()
        self.command_tickets: Counter[str] = Counter()
        self.repos: dict[str, str | None] = {}
        self.last_repo: str | None = None
        self.packs: dict[str, str] = {}
        self.full_packs = 0
        self.raw_prompts: dict[str, str] = {}
        self.synthetic: set[str] = set()
        self.asks: dict[str, bool] = {}
        self.answers: dict[tuple[str, int], str] = {}
        self.title: str | None = None
        self.last_msg = ""
        self.dirty = False
        self.last_remind_turn: int | None = None
        self.nosave = False
        self.private = False
        self.cards: list[str] = []
        self.rules_added = 0
        self.cap_raised = 0
        self.first_t: float | None = None
        self.last_t: float | None = None
        self.cwd: str | None = None
        self.transcript: str | None = None
        self.ended: Record | None = None

    @property
    def edit_total(self) -> int:
        return sum(self.edits.values())

    @property
    def meaningful(self) -> bool:
        return (
            self.edit_total > 0
            or self.tools >= self.rules.meaningful_tool_calls
            or self.turns >= self.rules.meaningful_user_turns
        )

    def apply(self, rec: Record) -> None:
        stamp = rec.get("t")
        if isinstance(stamp, (int, float)):
            self.first_t = stamp if self.first_t is None else self.first_t
            self.last_t = stamp
        was = self.meaningful
        handler = getattr(self, "on_" + str(rec.get("ev")), None)
        if handler:
            handler(rec)
        if not was and self.meaningful:
            self.dirty = True
        if (
            self.since_turns >= self.rules.dirty_user_turns
            or self.since_tools >= self.rules.dirty_tool_calls
        ):
            self.dirty = True
        if rec.get("ev") == "card" or rec.get("card"):
            self.clear_card(rec.get("card") or rec.get("path"))

    def clear_card(self, path: object) -> None:
        self.dirty = False
        self.since_turns = self.since_tools = 0
        if path:
            self.cards.append(str(path))

    def on_start(self, rec: Record) -> None:
        self.cwd = rec.get("cwd") or self.cwd
        if isinstance(rec.get("transcript"), str) and rec["transcript"]:
            self.transcript = rec["transcript"]
        for repo, ticket in rec.get("repos") or []:
            self.touch(repo, ticket, self.start_tickets)

    def on_private(self, rec: Record) -> None:
        self.private = True

    def on_title(self, rec: Record) -> None:
        if isinstance(rec.get("title"), str):
            self.title = rec["title"]

    def on_end(self, rec: Record) -> None:
        self.ended = rec

    def on_fail(self, rec: Record) -> None:
        self.fails += 1

    @property
    def prompts(self) -> dict[str, str]:
        return {pid: prompts.user_text(raw) for pid, raw in self.raw_prompts.items()}

    def on_prompt(self, rec: Record) -> None:
        pid = str(rec.get("pid") or "-")
        text = str(rec.get("text") or "")
        if rec.get("part", 0) == 0 and prompts.is_synthetic(text):
            self.synthetic.add(pid)
        if pid in self.synthetic:
            return
        self.raw_prompts[pid] = self.raw_prompts.get(pid, "") + text
        if rec.get("part", 0) == 0:
            self.turns += 1
            self.since_turns += 1
            self.nosave = self.nosave or bool(rec.get("nosave"))
            for key in (rec.get("tickets") or []) if self.rules.tickets.enabled else []:
                self.tickets[key] += 1
                self.typed_tickets[key] += 1
            for key in self.prompt_command_tickets(rec, text):
                self.command_tickets[key] += 1

    def prompt_command_tickets(self, rec: Record, text: str) -> list[str]:
        keys = rec.get("cmd_tickets")
        if not isinstance(keys, list):
            keys = self.rules.tickets.command_tickets(text)
        typed = rec.get("tickets") or []
        return [k for k in keys if k in typed]

    def ask_clean(self, tid: str) -> bool:
        return self.asks.get(tid) is True

    def on_ask(self, rec: Record) -> None:
        tid = str(rec.get("tid") or "")
        if tid:
            self.asks[tid] = self.asks.get(tid, True) and rec.get("clean") is True

    def on_answer(self, rec: Record) -> None:
        tid = str(rec.get("tid") or "")
        if not self.ask_clean(tid):
            return
        key = (tid, int(rec.get("k", 0)))
        self.answers[key] = self.answers.get(key, "") + str(rec.get("text") or "")

    def quote_sources(self) -> list[tuple[str, str]]:
        answers = [
            (re.sub(r"[^\w-]", "", tid)[-8:] or "-", text)
            for (tid, _), text in self.answers.items()
        ]
        return list(self.prompts.items()) + answers

    def on_tool(self, rec: Record) -> None:
        self.tools += 1
        self.since_tools += 1
        hits = None if rec.get("agent") else self.main_tickets
        for repo, ticket in rec.get("repos") or []:
            self.touch(repo, ticket, hits)
        for repo, ticket in rec.get("edits") or []:
            self.touch(repo, ticket, hits)
            self.edits[repo] += 1
            if ticket:
                self.ticket_edits[ticket] += 1
        if not rec.get("agent"):
            self.on_keys(rec)
        if rec.get("boundary"):
            self.dirty = True

    def on_keys(self, rec: Record) -> None:
        """Ticket keys seen by a main-session tool call, or named by a card (`ev: keys`)."""
        keys = rec.get("keys")
        if not isinstance(keys, list) or not self.rules.tickets.enabled:
            return
        for key in keys[:5]:
            if isinstance(key, str) and self.rules.tickets.find(key) == [key]:
                self.tickets[key] += 1
                self.main_tickets[key] += 1

    def touch(self, repo: str, ticket: str | None, source: Counter[str] | None = None) -> None:
        ticket = ticket if self.rules.tickets.enabled else None
        if ticket or repo not in self.repos:
            self.repos[repo] = ticket or self.repos.get(repo)
        if source is not None:
            self.last_repo = repo
        if ticket:
            self.tickets[ticket] += 1
            if source is not None:
                source[ticket] += 1

    def ticket_rank(self, key: str) -> tuple[int, int, int, int]:
        return (
            self.ticket_edits[key],
            self.typed_tickets[key],
            self.start_tickets[key],
            self.main_tickets[key],
        )

    def corroborated(self, key: str) -> bool:
        return bool(
            self.ticket_edits[key]
            or self.main_tickets[key]
            or self.start_tickets[key]
            or self.command_tickets[key]
        )

    def target_ticket(self) -> str | None:
        ranked = [k for k in self.tickets if self.corroborated(k)]
        return max(ranked, key=self.ticket_rank, default=None)

    @property
    def name_slug(self) -> str:
        return fold.slug(self.title, self.rules.preset) if self.title else ""

    @property
    def card_files(self) -> list[str]:
        return [p for p in self.cards[-1].split(",") if p] if self.cards else []

    def on_pack(self, rec: Record) -> None:
        self.packs[str(rec.get("key"))] = str(rec.get("mode"))
        if rec.get("mode") == "full":
            self.full_packs += 1

    def on_packs_reset(self, rec: Record) -> None:
        self.packs = {}
        self.full_packs = 0

    def on_remind(self, rec: Record) -> None:
        self.last_remind_turn = self.turns

    def on_stop(self, rec: Record) -> None:
        if isinstance(rec.get("msg"), str):
            self.last_msg = rec["msg"]

    def on_cap_raise(self, rec: Record) -> None:
        by = rec.get("by")
        if isinstance(by, int) and not isinstance(by, bool) and by > 0:
            self.cap_raised += by

    def on_rule_added(self, rec: Record) -> None:
        self.rules_added += 1

    def current_ticket(self) -> str | None:
        best = self.target_ticket()
        if not self.name_slug or (best and self.ticket_edits[best]):
            return best
        named = self.rules.tickets.find(self.title)
        return named[0] if named else None


def fold_lines(sid: str, lines: Iterable[bytes | str], rules: Rules | None = None) -> Segment:
    seg = Segment(sid, rules)
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            seg.apply(rec)
    return seg


def scan_synthetic(data: str) -> tuple[list[str], int, set[str]]:
    kept: list[str] = []
    dropped = 0
    pids: set[str] = set()
    parsed: list[tuple[str, Any]] = []
    for line in data.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            rec = None
        parsed.append((line, rec))
        if (
            isinstance(rec, dict)
            and rec.get("ev") == "prompt"
            and rec.get("part", 0) == 0
            and prompts.is_synthetic(str(rec.get("text") or ""))
        ):
            pids.add(str(rec.get("pid") or "-"))
    for line, rec in parsed:
        if (
            isinstance(rec, dict)
            and rec.get("ev") == "prompt"
            and str(rec.get("pid") or "-") in pids
        ):
            dropped += 1
        else:
            kept.append(line)
    return kept, dropped, pids
