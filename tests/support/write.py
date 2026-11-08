import contextlib
import io
import json
import os
from typing import Any

from juno import cli, config
from juno.adapters import ledgerio, queues
from tests.support import PYTHON, SandboxCase

SID = "57fb7685-9be8-4bc7-a435-47aa50ccd35f"


class WriteCase(SandboxCase):
    TICKETS = False

    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)
        if self.TICKETS:
            self.set_config('[jira]\nprefixes = ["ABC"]\n')
        self.sid = SID
        self.env = dict(
            self.sb.env,
            JUNO_TEST_INTERACTIVE="1",
            CLAUDE_CODE_SESSION_ID=self.sid,
            JUNO_PYTHON=PYTHON,
        )
        self.prompt("hello there, this is the first typed prompt", "pid00001")

    def set_config(self, toml: str) -> None:
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write("\n" + toml + "\n")

    @property
    def cfg(self) -> config.Config:
        return config.load(self.env)

    def prompt(self, text: str, pid: str, sid: str | None = None, **extra: Any) -> None:
        rec = {"ev": "prompt", "pid": pid, "part": 0, "text": text, **extra}
        ledgerio.append(self.cfg, sid or self.sid, rec)

    def answer(self, text: str, tid: str = "toolu_answer01") -> None:
        ledgerio.append(self.cfg, self.sid, {"ev": "ask", "tid": tid, "clean": True})
        ledgerio.append(self.cfg, self.sid, {"ev": "answer", "tid": tid, "k": 0, "text": text})

    def juno(self, *argv: str, env: dict[str, str] | None = None) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(list(argv), dict(self.env, **(env or {})))
        return code, buffer.getvalue()

    def vp(self, rel: str) -> str:
        return os.path.join(self.sb.vault, *rel.split("/"))

    def put(self, rel: str, text: str) -> str:
        path = self.vp(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def get(self, rel: str) -> str:
        try:
            with open(self.vp(rel), encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return ""

    def body_file(self, text: str, name: str = "body.md") -> str:
        os.makedirs(self.sb.state, exist_ok=True)
        path = os.path.join(self.sb.state, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def seed_queue(self, queue: queues.Queue, items: list[dict[str, Any]]) -> None:
        queues.write(self.cfg, queue, items)

    def queue_status(self, queue: queues.Queue) -> dict[str, str]:
        return {str(i["id"]): str(i.get("status")) for i in queues.read(self.cfg, queue)}

    def events(self) -> list[dict[str, Any]]:
        path = os.path.join(self.sb.state, "ledgers", self.sid + ".jsonl")
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines()]
