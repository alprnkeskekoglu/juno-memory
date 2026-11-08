import os
import time
import unittest

from juno.adapters import stubs, writelog
from juno.core import writegate
from tests.support.cardcase import KEY, CardCase, card
from tests.support.hooks import SID


class OneWritePath(CardCase):
    def undo(self, wid: str) -> tuple[int, str]:
        import contextlib
        import io

        from juno import cli

        buffer = io.StringIO()
        env = dict(self.env, CLAUDE_CODE_SESSION_ID=SID)
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["undo", wid], env)
        return code, buffer.getvalue()

    def records(self) -> list[dict[str, object]]:
        return writelog.read(self.cfg())

    def test_card_run_is_logged_backed_up_and_undoable(self) -> None:
        self.write(f"handoffs/{KEY}.md", f"# Handoffs · {KEY}\n\n" + card(seg="deadbeef"))
        self.dirty()
        self.run_card(card(body="First version."))
        (rec,) = self.records()
        self.assertEqual(
            (rec["verb"], rec["file"], rec["sid"]), ("card", f"handoffs/{KEY}.md", SID)
        )
        self.assertTrue(os.listdir(os.path.join(self.sb.state, "backups")))
        code, out = self.undo(str(rec["id"]))
        self.assertEqual(code, 0, out)
        self.assertNotIn("First version.", self.handoff())
        self.assertIn("deadbeef", self.handoff())

    def test_card_run_is_not_stopped_by_the_session_write_cap(self) -> None:
        self.dirty()
        for n in range(writegate.RUN_MAX):
            writelog.append(self.cfg(), {"id": f"w-{n:08x}", "sid": SID, "verb": "note"})
        self.assertEqual(self.run_card(card())["status"], "ok")

    def test_card_run_refuses_a_symlinked_handoff(self) -> None:
        outside = os.path.join(self.sb.root, "outside.md")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("keep\n")
        os.symlink(outside, self.vp(f"handoffs/{KEY}.md"))
        self.dirty()
        row = self.run_card(card())
        self.assertEqual(row["status"], "failed")
        with open(outside, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "keep\n")

    def test_card_run_refuses_private_data(self) -> None:
        self.dirty()
        row = self.run_card(card(body="Mailed jane.doe@corp.com about it."))
        self.assertEqual(row["status"], "failed")
        self.assertIn("private data", str(row["error"]))
        self.assertEqual(self.handoff(), "")

    def test_stub_is_logged_and_undoable(self) -> None:
        self.dirty()
        self.assertTrue(stubs.write_stub(self.cfg(), self.seg(), time.time()))
        (rec,) = self.records()
        self.assertEqual((rec["verb"], rec["file"]), ("card", f"handoffs/{KEY}.md"))
        self.assertIn("auto: true", self.handoff())
        self.assertEqual(self.undo(str(rec["id"]))[0], 0)
        self.assertEqual(self.handoff(), "")

    def test_stub_refuses_a_symlinked_handoff(self) -> None:
        outside = os.path.join(self.sb.root, "outside.md")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("keep\n")
        os.symlink(outside, self.vp(f"handoffs/{KEY}.md"))
        self.dirty()
        self.assertEqual(stubs.write_stub(self.cfg(), self.seg(), time.time()), [])
        with open(outside, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "keep\n")


if __name__ == "__main__":
    unittest.main()
