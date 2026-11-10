import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import unittest

from juno.adapters import status as io
from juno.core import status
from tests.e2e import harness
from tests.support import REPO, Sandbox, e2e

SID = "2b1f0c7e-1d2a-4c3b-9e8f-0a1b2c3d4e5f"


def tree_hash(root: str) -> str:
    digest = hashlib.sha256()
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                digest.update(os.path.relpath(path, root).encode() + b"\0" + handle.read())
    return digest.hexdigest()


def state_files(root: str) -> dict[str, str]:
    found = {}
    for base, _, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                found[os.path.relpath(path, root)] = hashlib.sha256(handle.read()).hexdigest()
    return found


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        done = harness.init_with_bin(self.sb)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def put(self, rel: str, text: str) -> None:
        path = os.path.join(self.sb.vault, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)


@e2e
class DashboardPanels(Case):
    def test_page_has_the_panels_and_leaves_the_vault_untouched(self) -> None:
        self.put("threads.md", "- [app] ship the thing 2026-01-01 · since 2025-12-01\n")
        self.put("handoffs/app.md", "## 2026-10-25 11:00 · - · a card · bbbbbbbb\n")
        self.put(
            "backlog/p1-item.md",
            "---\ntitle: Fix the sync job\nstatus: next\npriority: p1\n"
            "added: 2026-10-01\nupdated: 2026-10-01\n---\n\n## Task\nx\n",
        )
        projects = os.path.join(self.sb.root, "projects")
        os.makedirs(projects)
        self.sb.env["JUNO_CLAUDE_PROJECTS"] = projects
        before = tree_hash(self.sb.vault)
        state_before = state_files(self.sb.state)
        done = harness.shim(self.sb, "dashboard")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        page_path = done.stdout.strip()
        self.assertEqual(page_path, os.path.join(self.sb.state, "dashboard", "index.html"))
        with open(page_path, encoding="utf-8") as handle:
            page = handle.read()
        titles = [
            re.sub(r"<button.*?</button>", "", t) for t in re.findall(r"<h2>(.*?)</h2>", page)
        ]
        for name in ("Recall over time", "Experiments", "Writes", "Cost", "Health"):
            self.assertIn(name, titles)
        for word in ("alarm", "mentee", "Saga"):
            self.assertNotIn(word, page.lower() if word.islower() else page)
        self.assertEqual(tree_hash(self.sb.vault), before)
        after = state_files(self.sb.state)
        changed = [rel for rel, digest in after.items() if state_before.get(rel) != digest]
        self.assertTrue(changed)
        for rel in changed:
            self.assertTrue(rel.startswith("dashboard" + os.sep), rel)
        self.assertEqual(set(state_before) - set(after), set())

    def test_language_follows_the_preset(self) -> None:
        os.makedirs(os.path.dirname(self.sb.config), exist_ok=True)
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write('\n[language]\npreset = "tr"\n')
        done = harness.shim(self.sb, "dashboard", "--json")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)["language"], "tr")


@e2e
@unittest.skipUnless(shutil.which("node"), "node not installed")
class StatusJsonLoader(Case):
    def test_mod_loader_reads_the_written_status(self) -> None:
        built = status.build_status(
            sid=SID,
            now=time.time(),
            previous={},
            repo="app",
            ticket=None,
            card_dirty=True,
            card_auto=False,
            card_path="handoffs/app.md",
            packs_injected=1,
            packs_cap=4,
            rules_added_segment=0,
            writes=0,
            write_cap=50,
            errors_24h=3,
            last_error=None,
        )
        io.write_status(self.sb.state, built)
        script = os.path.join(REPO, "ui", "test", "read-status.mjs")
        done = subprocess.run(
            ["node", script, self.sb.home, self.sb.state, SID],
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        got = json.loads(done.stdout)
        self.assertEqual((got["mine"], got["status"]), (True, built))


if __name__ == "__main__":
    unittest.main()
