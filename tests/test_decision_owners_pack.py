import dataclasses
import difflib
import json
import os
import shutil
import unittest

from juno.adapters import store, vaultview
from juno.adapters.vaultview import DiskVault
from juno.core import inject, migrate, packs
from juno.core.ledger import Segment
from juno.core.packs import Caps
from juno.handlers import delivery
from tests.support import decision_vault as dv
from tests.support.write import WriteCase

LINKED_LINE = "- Follow the layout in [[notes/design]] (user pid00001, 2026-11-01)"
LINKED_REPO_LINE = "- Keep it aligned with [[notes/design]] (user pid00001, 2026-11-01)"
STALE_FILES = ("projects/alpha.md", "projects/beta.md", "tasks/ABC-1.md")
REPOS = ("alpha", "beta", "app-2", "gamma", "delta", *dv.OWNERLESS)
BIG = 1_000_000
MARKER_HEAD = inject.MARKER.split("{")[0]


def snapshot(root: str) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for base, _, files in os.walk(root):
        for name in files:
            with open(os.path.join(base, name), "rb") as handle:
                found[os.path.relpath(base + "/" + name, root)] = handle.read()
    return found


def report(before: dict[str, str], after: dict[str, str]) -> str:
    for key in before:
        if before[key] != after.get(key):
            diff = difflib.unified_diff(
                before[key].splitlines(),
                after.get(key, "").splitlines(),
                "before",
                "after",
                lineterm="",
            )
            return f"first difference at {key}\n" + "\n".join(diff)
    return "keys differ"


class Proof(WriteCase):
    """Every pack a session would get is byte-identical before and after the decision move."""

    TICKETS = True

    def setUp(self) -> None:
        super().setUp()
        self.set_config("[links]\nlinked_block = true")
        dv.build(self.sb.vault)
        self.put("notes/design.md", "# Design\n\nThe layout.\n")
        self.put("notes/index.md", "# Index\n\nSee [[ABC-2]] and [[alpha]] and [[ABC-20]].\n")
        self.add_line("decisions/ABC-2.md", LINKED_LINE)
        self.add_line("decisions/ABC-20.md", LINKED_LINE)
        self.add_line("decisions/alpha.md", LINKED_REPO_LINE)
        for rel in ("decisions/ABC-2.md", "decisions/alpha.md", "decisions/payments-design.md"):
            for n in range(12):
                self.add_line(
                    rel, dv.line(f"long filler choice number {n} with padding words " * 2)
                )
        for rel in STALE_FILES:
            self.mark_stale(rel)

    def add_line(self, rel: str, text: str) -> None:
        old = self.get(rel)
        self.put(rel, old.replace("\n\n## History", "\n" + text + "\n\n## History", 1))
        os.utime(self.vp(rel), (dv.SOURCE_MTIME, dv.SOURCE_MTIME))

    def mark_stale(self, rel: str) -> None:
        sig = store.file_sig(self.vp(rel)) or [0, 0]
        path = os.path.join(self.sb.state, "stale.json")
        data = store.read_json_dict(path) if os.path.exists(path) else {}
        files = data.get("files") if isinstance(data.get("files"), dict) else {}
        files[rel] = {"stale": True, "since": "2026-10-01", "sig": sig}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"v": 1, "generated": 1, "files": files}, handle)

    def renders(self) -> dict[str, str]:
        cfg = self.cfg
        vaultview.sync_retired(cfg)
        view = DiskVault(cfg)
        caps = Caps.from_config(cfg)
        small = dataclasses.replace(caps, pack_chars=600)
        cards = packs.all_cards(view)
        wanted = [("ticket", k) for k in [*dv.TICKETS, "XYZ-9"]] + [("repo", r) for r in REPOS]
        out: dict[str, str] = {}
        for n, (kind, key) in enumerate(wanted):
            seg = Segment(f"sid-{n}")
            out[f"{kind}:{key}:deliver"] = delivery.deliver_packs(
                cfg, view, seg, [(kind, key)], BIG
            )
            out[f"{kind}:{key}:600"] = packs.render_pack(view, small, kind, key, cards)
            out[f"{kind}:{key}:pointer"] = packs.pack_pointer(view, kind, key)
            out[f"{kind}:{key}:nb"] = "\n".join(
                view.linked_paths(kind, key, packs.own_files(kind, key))
            )
        for name, pair in (("ticket", ("ticket", "ABC-2")), ("repo", ("repo", "alpha"))):
            out[f"subagent:{name}"] = packs.subagent_block(view, caps, [pair], lambda n: [])
        return out

    def run_migrate(self, *argv: str) -> None:
        code, out = self.juno("migrate", *argv)
        self.assertEqual(code, 0, out)

    def check(self, before: dict[str, str]) -> None:
        after = self.renders()
        if before != after:
            self.fail(report(before, after))

    def test_packs_are_identical_after_migrate_decisions_and_the_step(self) -> None:
        vault_copy = self.sb.vault + ".orig"
        shutil.copytree(self.sb.vault, vault_copy)
        stale_copy = os.path.join(self.sb.state, "stale.orig")
        shutil.copy2(os.path.join(self.sb.state, "stale.json"), stale_copy)
        before = self.renders()
        self.assertEqual(len(before), (len(dv.TICKETS) + 1 + len(REPOS)) * 4 + 2)
        self.assertTrue(all(before[f"ticket:{k}:deliver"] for k in dv.TICKETS))
        self.assertTrue(any("may be stale since" in v for v in before.values()))
        self.assertTrue(any("Linked" in v and "notes/index.md" in v for v in before.values()))
        self.assertTrue(any(MARKER_HEAD in v for k, v in before.items() if k.endswith(":600")))

        self.run_migrate("--decisions")
        self.check(before)
        moved = snapshot(self.sb.vault)
        self.run_migrate("--decisions")
        self.assertEqual(snapshot(self.sb.vault), moved)
        for topic in ("gamma", "delta", *dv.OWNERLESS):
            self.assertTrue(os.path.exists(self.vp(f"decisions/{topic}.md")), topic)

        shutil.rmtree(self.sb.vault)
        shutil.copytree(vault_copy, self.sb.vault)
        shutil.copy2(stale_copy, os.path.join(self.sb.state, "stale.json"))
        links = os.path.join(self.sb.state, "links.json")
        if os.path.exists(links):
            os.remove(links)
        toml = self.vp("juno-brain.toml")
        with open(toml, encoding="utf-8") as handle:
            text = handle.read()
        with open(toml, "w", encoding="utf-8") as handle:
            handle.write(migrate.write_schema(text, 5))
        self.run_migrate()
        self.assertEqual(migrate.read_schema(self.get("juno-brain.toml")), 6)
        self.check(before)
        self.assertEqual(snapshot(self.sb.vault).keys(), moved.keys())


if __name__ == "__main__":
    unittest.main()
