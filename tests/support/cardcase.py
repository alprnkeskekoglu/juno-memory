import json
import os
import time
from typing import Any
from unittest import mock

from juno import config
from juno.adapters import cardjob, cardrun, ledgerio
from juno.core.ledger import Segment
from tests.support import fakeclaude
from tests.support.hooks import SID, HookCase

SEG8 = SID[:8]
KEY = "ABC-111111"
PROMPT = "fix the refund rounding in src/money.py"


def card(
    key: str = KEY,
    seg: str = SEG8,
    body: str = "Fixed the refund rounding in src/money.py; tests pass.\nNext: open the PR.",
    learned: str = "rounding belongs in the money type",
    extra: str = "",
) -> str:
    return (
        f"## 2026-01-01 00:00 · {key} · refund rounding · {seg}\n"
        f"Repos: billing-api\nTickets: {KEY}\n{body}\n{extra}Learned: {learned}\n"
    )


def with_extract(text: str, data: dict[str, Any] | str) -> str:
    raw = data if isinstance(data, str) else json.dumps(data)
    return text + "\n---JUNO-EXTRACT---\n" + raw + "\n"


class CardCase(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.make_repo("billing-api", f"feature/{KEY}")
        self.calls: list[dict[str, Any]] = []
        patch = mock.patch.dict(os.environ, self.env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def redirty(self, sid: str = SID) -> None:
        for i in range(10):
            self.prompt(f"another step {i}", sid)

    def cfg(self) -> config.Config:
        return config.load(dict(self.env))

    def prompt(self, text: str = PROMPT, sid: str = SID) -> None:
        self.hook(
            "user_prompt_submit", self.payload("UserPromptSubmit", prompt=text, session_id=sid)
        )

    def edit(self, sid: str = SID) -> None:
        path = os.path.join(self.repo, "a.txt")
        self.hook(
            "post_tool_use",
            self.payload(
                "PostToolUse",
                session_id=sid,
                tool_name="Edit",
                tool_input={"file_path": path},
                tool_response={},
            ),
        )

    def dirty(self, text: str = PROMPT, sid: str = SID) -> None:
        self.prompt(text, sid)
        self.edit(sid)

    def seg(self, sid: str = SID) -> Segment:
        return ledgerio.fold(self.cfg(), sid)

    def job(self, **extra: Any) -> dict[str, Any]:
        return {"sid": SID, "status": "pending", "t": time.time(), "max_budget_usd": 0.3, **extra}

    def model(self, text: str, cost: float = 0.05) -> Any:
        def call(cfg: config.Config, prompt: str, budget: float) -> tuple[str, float]:
            self.calls.append({"prompt": prompt, "budget": budget})
            return text, cost

        return call

    def run_card(self, text: str, cost: float = 0.05, sid: str = SID, **job: Any) -> dict[str, Any]:
        cfg = self.cfg()
        cardrun.run_locked(cfg, sid, self.job(sid=sid, **job), self.model(text, cost))
        return cardjob.read_runs(cfg)[-1]

    def sections(self, row: dict[str, Any]) -> dict[str, str]:
        sections = row["sections"]
        assert isinstance(sections, dict)
        return sections

    def handoff(self, name: str = KEY) -> str:
        return (
            self.read(f"handoffs/{name}.md")
            if os.path.exists(self.vp(f"handoffs/{name}.md"))
            else ""
        )

    def fake(self, text: str, cost: float = 0.05) -> None:
        fakeclaude.answer(self.env, text, cost)

    def fake_call(self) -> dict[str, object]:
        return fakeclaude.call_log(self.env)
