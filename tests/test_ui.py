import json
import os
import shutil
import subprocess
import time
import unittest

from juno.adapters import status as io
from juno.core import status
from tests.support import REPO, SandboxCase

NODE = shutil.which("node")
UI = os.path.join(REPO, "ui")
SID = "2b1f0c7e-1d2a-4c3b-9e8f-0a1b2c3d4e5f"


def node(*argv: str) -> subprocess.CompletedProcess[str]:
    assert NODE
    return subprocess.run(
        [NODE, *argv], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL
    )


@unittest.skipUnless(NODE, "node not installed")
class Mod(SandboxCase):
    def test_format_tests_pass(self) -> None:
        done = node(os.path.join(UI, "test", "format.test.mjs"))
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "ok"), done.stderr)

    def read(self, sid: str = SID, state: str | None = None) -> dict:
        done = node(os.path.join(UI, "test", "read-status.mjs"), self.sb.home, state or "", sid)
        self.assertEqual(done.returncode, 0, done.stderr)
        found: dict = json.loads(done.stdout)
        return found

    def built(self, sid: str, ticket: str) -> dict:
        return status.build_status(
            sid=sid,
            now=time.time(),
            previous={},
            repo="app",
            ticket=ticket,
            card_dirty=True,
            card_auto=False,
            card_path="handoffs/app.md",
            packs_injected=1,
            packs_cap=4,
            rules_added_segment=0,
            writes=0,
            write_cap=50,
            errors_24h=2,
            last_error=None,
        )

    def test_loader_reads_what_write_status_wrote(self) -> None:
        io.write_status(self.sb.state, self.built(SID, "ABC-1"))
        got = self.read(SID, self.sb.state)
        self.assertEqual(
            (got["mine"], got["sid"], got["status"]["ticket"], got["status"]["errors_24h"]),
            (True, SID, "ABC-1", 2),
        )

    def test_loader_ignores_another_sessions_shared_file(self) -> None:
        io.write_status(self.sb.state, self.built("other-session", "ABC-9"))
        got = self.read(SID, self.sb.state)
        self.assertEqual((got["mine"], got["status"]), (False, None))

    def test_loader_finds_the_default_state_dir_under_home(self) -> None:
        state = os.path.join(self.sb.home, "Library", "Application Support", "juno")
        io.write_status(state, self.built(SID, "ABC-2"))
        self.assertEqual(self.read(SID)["status"]["ticket"], "ABC-2")
        shutil.rmtree(state)
        state = os.path.join(self.sb.home, ".local", "state", "juno")
        io.write_status(state, self.built(SID, "ABC-3"))
        self.assertEqual(self.read(SID)["status"]["ticket"], "ABC-3")


class Files(unittest.TestCase):
    def test_manifest_and_hooks_json(self) -> None:
        with open(os.path.join(UI, ".claude-plugin", "plugin.json"), encoding="utf-8") as h:
            self.assertEqual(json.load(h)["name"], "juno-ui")
        with open(os.path.join(UI, "hooks", "hooks.json"), encoding="utf-8") as h:
            self.assertEqual(json.load(h), {"modules": ["./register.js"]})

    def test_mod_has_no_v1_names(self) -> None:
        for name in ("hooks/register.js", "README.md", "test/format.test.mjs"):
            with open(os.path.join(UI, name), encoding="utf-8") as h:
                text = h.read()
            for banned in ("SAGA_", "saga"):
                self.assertNotIn(banned, text, name)

    def test_mod_only_reads(self) -> None:
        with open(os.path.join(UI, "hooks", "register.js"), encoding="utf-8") as h:
            text = h.read()
        for needle in ("fs.write", "fs.remove", "fs.mkdir", "$.proc", "child_process"):
            self.assertNotIn(needle, text)


if __name__ == "__main__":
    unittest.main()
