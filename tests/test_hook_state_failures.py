import contextlib
import errno
import io
import json
import os
import tempfile
import unittest
from typing import Any
from unittest import mock

from juno import config, hookcore
from juno.adapters import fs, ledgerio, prune, store
from tests.support.write import SID, WriteCase
from tests.test_hooks import HookCase, load_entry

STATE_ERRNOS = (errno.ENOSPC, errno.EACCES, errno.EPERM, errno.EROFS)
NOTE = "The export job times out above ten thousand rows"


def boom(code: int) -> Any:
    def raiser(*args: object, **kwargs: object) -> None:
        raise OSError(code, os.strerror(code))

    return raiser


def drop_tmp_flag(env: dict[str, str]) -> None:
    """The unrecorded flag is also written to /tmp; remove it after the test."""
    marker = ledgerio.unrecorded_marker(config.load(dict(env, TMPDIR="")), SID)
    with contextlib.suppress(OSError):
        os.remove(os.path.join("/tmp", os.path.basename(marker or "")))


class StateDirUnwritable(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(drop_tmp_flag, self.sb.env)
        rules = os.path.join(self.sb.vault, "rules", "global.md")
        with open(rules, "w", encoding="utf-8") as handle:
            handle.write('- 2026-01-01 · Keep answers short · src: user abcd1234 · "quote"\n')

    def interactive(self, **extra: str) -> dict[str, str]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return super().interactive(TMPDIR=tmp.name, **extra)

    def test_session_start_keeps_rules_when_state_is_unwritable(self) -> None:
        for code in STATE_ERRNOS:
            with (
                self.subTest(errno=code),
                mock.patch.object(prune, "prune_state", boom(code)),
                mock.patch.object(ledgerio, "append", boom(code)),
            ):
                payload = json.dumps({"session_id": SID, "cwd": self.sb.home})
                _, out = self.call("session_start", self.interactive(), payload)
                body = json.loads(out)["hookSpecificOutput"]["additionalContext"]
                self.assertIn("Keep answers short", body)
                self.assertIn("could not write its state dir", body)

    def test_unrelated_oserror_is_not_swallowed(self) -> None:
        with mock.patch.object(prune, "prune_state", boom(errno.EIO)):
            _, out = self.call("session_start", self.interactive(), json.dumps({"session_id": SID}))
        self.assertEqual(out, "")

    def test_user_prompt_keeps_context_and_stop_stays_silent(self) -> None:
        payload = json.dumps({"session_id": SID, "prompt": "hello there", "prompt_id": "p1"})
        with mock.patch.object(ledgerio, "append", boom(errno.EACCES)):
            _, out = self.call("user_prompt_submit", self.interactive(), payload)
            self.assertIn("could not write its state dir", out)
            stop = json.dumps({"session_id": SID, "last_assistant_message": "done"})
            self.assertEqual(self.call("stop", self.interactive(), stop)[1], "")

    def test_log_error_cannot_raise_and_records_errno(self) -> None:
        cfg = config.load(self.interactive())
        exc = OSError(errno.ENOSPC, "No space left")
        with mock.patch.object(fs, "append_log", boom(errno.ENOSPC)):
            store.log_error(cfg, "stop", exc)
        lines: list[str] = []
        with mock.patch.object(fs, "append_log", lambda path, line, limit: lines.append(line)):
            store.log_error(cfg, "stop", exc)
        self.assertIn("errno=28", lines[0])


class NosaveFailsClosed(WriteCase):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(drop_tmp_flag, self.env)

    def test_unrecorded_nosave_prompt_blocks_later_vault_writes(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env["TMPDIR"] = tmp.name
        entry = load_entry("user_prompt_submit")
        payload = json.dumps(
            {"session_id": self.sid, "prompt": "keep this out [nosave]", "prompt_id": "p2"}
        )
        with (
            mock.patch.dict(os.environ, self.env, clear=True),
            mock.patch("sys.stdin", io.StringIO(payload)),
            mock.patch("sys.stdout", io.StringIO()),
            mock.patch.object(ledgerio, "append", boom(errno.ENOSPC)),
        ):
            entry.hookcore.run("user_prompt_submit", entry.handle)
        for argv in (("note", "--slug", "export-timeout", NOTE), ("capture", NOTE)):
            code, out = self.juno(*argv)
            self.assertEqual(code, 2, out)
        self.assertEqual([n for n in os.listdir(self.vp("notes")) if n.endswith(".md")], [])
        self.assertFalse(os.path.exists(self.vp("inbox.md")) and NOTE in self.get("inbox.md"))

    def test_nosave_flag_survives_a_different_tmpdir_in_the_verb_process(self) -> None:
        hook_tmp = tempfile.TemporaryDirectory()
        verb_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(hook_tmp.cleanup)
        self.addCleanup(verb_tmp.cleanup)
        entry = load_entry("user_prompt_submit")
        payload = json.dumps(
            {"session_id": self.sid, "prompt": "keep this out [nosave]", "prompt_id": "p2"}
        )
        with (
            mock.patch.dict(os.environ, dict(self.env, TMPDIR=hook_tmp.name), clear=True),
            mock.patch("sys.stdin", io.StringIO(payload)),
            mock.patch("sys.stdout", io.StringIO()),
            mock.patch.object(ledgerio, "append", boom(errno.ENOSPC)),
        ):
            entry.hookcore.run("user_prompt_submit", entry.handle)
        argv = ("note", "--slug", "export-timeout", NOTE)
        code, out = self.juno(*argv, env={"TMPDIR": verb_tmp.name})
        self.assertEqual(code, 2, out)
        self.assertEqual([n for n in os.listdir(self.vp("notes")) if n.endswith(".md")], [])


class LoneSurrogate(HookCase):
    def run_bytes(self, event: str, raw: bytes) -> str:
        stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="ascii", errors="strict")
        entry = load_entry(event)
        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, self.interactive(), clear=True),
            mock.patch("sys.stdin", stdin),
            mock.patch("sys.stdout", out),
        ):
            entry.hookcore.run(event, entry.handle)
        return out.getvalue()

    def ledger_text(self) -> str:
        path = ledgerio.ledger_path(config.load(self.interactive()), SID)
        with open(path or "", "rb") as handle:
            return handle.read().decode()

    def test_escaped_lone_surrogate_still_records_and_answers(self) -> None:
        raw = '{"session_id": "%s", "prompt": "cut off emoji \\ud83d", "prompt_id": "p1"}' % SID
        self.run_bytes("user_prompt_submit", raw.encode())
        self.assertIn("cut off emoji �", self.ledger_text())
        raw = '{"session_id": "%s", "cwd": "/x", "source": "startup\\ud83d"}' % SID
        out = self.run_bytes("session_start", raw.encode())
        self.assertIn("hookSpecificOutput", out)

    def test_invalid_utf8_bytes_keep_the_session_id(self) -> None:
        raw = (
            b'{"session_id": "' + SID.encode() + b'", "prompt": "bad \xff byte", "prompt_id": "p1"}'
        )
        self.run_bytes("user_prompt_submit", raw)
        self.assertIn("bad � byte", self.ledger_text())

    def test_surrogate_in_output_cannot_crash_stdout(self) -> None:
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            hookcore.emit({"x": "a\ud83d"})
        self.assertIn("a�", out.getvalue())


if __name__ == "__main__":
    unittest.main()
