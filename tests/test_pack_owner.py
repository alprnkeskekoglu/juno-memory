import re
import unittest

from juno.core import owners, packs, vaultfmt
from juno.core.packs import Caps
from tests.test_packs import FakeVault

L1 = "- use postgres (user abcd1234, 2026-01-01)"
L2 = "- ship on tuesdays (user abcd1234, 2026-01-02)"
L3 = "- cache for an hour (user abcd1234, 2026-01-03)"
PROFILE = "# a\n\n## Purpose\nP\n\n## Gotchas\n- g1\n\n## How to run\n- run it\n"
TASK = "---\njira: ABC-1\ntitle: T\n---\n\n## Notes\n- note one\n"


def legacy(*lines: str) -> str:
    return "## Current\n" + "\n".join(lines) + "\n\n## History\n- old\n"


def with_decisions(doc: str, *lines: str) -> str:
    return owners.set_section_lines(owners.ensure_section(doc, "Decisions"), "Decisions", [*lines])


def render(view: FakeVault, kind: str, key: str, caps: Caps | None = None) -> str:
    return packs.render_pack(view, caps or Caps(), kind, key, packs.all_cards(view))


def first_block(text: str) -> tuple[str, str]:
    match = re.search(
        r"CONTEXT \(data, not instructions\):\n\[(Decisions · [^\]]+)\]\n(.*?)(?=\n\[|\Z)",
        text,
        re.S,
    )
    assert match, text
    return match[1], match[2]


class Body(unittest.TestCase):
    def test_owner_only_legacy_only_and_split_render_the_same_body(self) -> None:
        owner = FakeVault({"projects/a.md": with_decisions(PROFILE, L1, L2, L3)})
        old = FakeVault({"projects/a.md": PROFILE, "decisions/a.md": legacy(L1, L2, L3)})
        split = FakeVault(
            {"projects/a.md": with_decisions(PROFILE, L1, L2), "decisions/a.md": legacy(L3)}
        )
        bodies = [first_block(render(v, "repo", "a")) for v in (owner, old, split)]
        self.assertEqual(bodies[0], ("Decisions · projects/a.md", "\n".join([L1, L2, L3])))
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(bodies[0], bodies[2])

    def test_exact_duplicates_collapse_and_near_duplicates_stay(self) -> None:
        near = L1.replace("postgres", "postgresql")
        view = FakeVault(
            {"projects/a.md": with_decisions(PROFILE, L1), "decisions/a.md": legacy(L1, near)}
        )
        self.assertEqual(first_block(render(view, "repo", "a"))[1], "\n".join([L1, near]))

    def test_hidden_legacy_contributes_nothing(self) -> None:
        hidden = "---\nvisibility: private\n---\n" + legacy(L2)
        view = FakeVault({"projects/a.md": with_decisions(PROFILE, L1), "decisions/a.md": hidden})
        self.assertEqual(first_block(render(view, "repo", "a"))[1], L1)

    def test_app_2_repo_reads_the_ticket_style_legacy_file(self) -> None:
        view = FakeVault({"projects/app-2.md": PROFILE, "decisions/APP-2.md": legacy(L1)})
        self.assertEqual(
            first_block(render(view, "repo", "app-2")), ("Decisions · projects/app-2.md", L1)
        )

    def test_profile_decisions_do_not_leak_into_head_sections(self) -> None:
        view = FakeVault({"projects/a.md": with_decisions(PROFILE, L1)})
        text = render(view, "repo", "a")
        self.assertEqual(text.count(L1), 1)
        self.assertIn("[Gotchas · projects/a.md]\n- g1\n", text)

    def test_ticket_notes_are_read_by_name_when_decisions_come_first(self) -> None:
        view = FakeVault({"tasks/ABC-1.md": with_decisions(TASK, L1)})
        text = render(view, "ticket", "ABC-1")
        self.assertIn("[Notes · tasks/ABC-1.md]\n- note one", text)
        self.assertEqual(text.count(L1), 1)


class Title(unittest.TestCase):
    def title(self, files: dict[str, str], kind: str, key: str) -> str:
        return first_block(render(FakeVault(files), kind, key))[0]

    def test_ticket_title_is_always_the_task_file(self) -> None:
        old = {"decisions/ABC-1.md": legacy(L1)}
        for extra in ({}, {"tasks/ABC-1.md": TASK}, {"tasks/ABC-1.md": "---\njira: ABC-1\n---\n"}):
            self.assertEqual(
                self.title({**old, **extra}, "ticket", "ABC-1"), "Decisions · tasks/ABC-1.md"
            )

    def test_repo_title_follows_the_profile(self) -> None:
        old = {"decisions/a.md": legacy(L1)}
        self.assertEqual(
            self.title({**old, "projects/a.md": PROFILE}, "repo", "a"), "Decisions · projects/a.md"
        )
        hidden = "---\nvisibility: private\n---\n" + PROFILE
        self.assertEqual(
            self.title({**old, "projects/a.md": hidden}, "repo", "a"), "Decisions · decisions/a.md"
        )
        self.assertEqual(self.title(old, "repo", "a"), "Decisions · decisions/a.md")

    def test_the_title_never_carries_the_stale_mark(self) -> None:
        view = FakeVault({"projects/stale.md": with_decisions(PROFILE, L1)})
        text = render(view, "repo", "stale")
        self.assertIn("[Decisions · projects/stale.md]", text)
        self.assertIn("[Gotchas · projects/stale.md (may be stale", text)


class Hint(unittest.TestCase):
    def test_hint_for_missing_file_and_for_a_stub_without_title(self) -> None:
        stub = "---\njira: ABC-1\ncreated: 2026-01-01\n---\n\n## Decisions\n\n## Notes\n"
        for files in ({}, {"tasks/ABC-1.md": stub}):
            view = FakeVault({**files, "decisions/ABC-1.md": legacy(L1)})
            self.assertIn("[Task file · tasks/ABC-1.md]", render(view, "ticket", "ABC-1"))
        view = FakeVault({"tasks/ABC-1.md": TASK})
        self.assertNotIn("Task file", render(view, "ticket", "ABC-1"))


class Pointer(unittest.TestCase):
    def test_pointer_names_the_owner_not_an_existing_legacy_file(self) -> None:
        view = FakeVault({"decisions/ABC-1.md": legacy(L1)})
        text = packs.pack_pointer(view, "ticket", "ABC-1")
        self.assertIn("tasks/ABC-1.md", text)
        self.assertNotIn("decisions/", text)


def migrate(files: dict[str, str], owner_rel: str, legacy_rel: str) -> dict[str, str]:
    """The vault after `juno migrate --decisions` for one owner."""
    out = dict(files)
    doc = out.get(owner_rel, "")
    merged = owners.merged_decisions(
        vaultfmt.section(doc, "Decisions"), vaultfmt.decision_current(out[legacy_rel])
    )
    out[owner_rel] = with_decisions(doc, *merged)
    del out[legacy_rel]
    return out


class Migration(unittest.TestCase):
    CASES = (
        ("repo", "a", "projects/a.md", "decisions/a.md", PROFILE),
        ("ticket", "ABC-1", "tasks/ABC-1.md", "decisions/ABC-1.md", TASK),
        ("ticket", "ABC-2", "tasks/ABC-2.md", "decisions/ABC-2.md", ""),
    )

    def test_packs_and_pointers_are_identical_before_and_after(self) -> None:
        for kind, key, owner_rel, legacy_rel, doc in self.CASES:
            files = {legacy_rel: legacy(L1, L2, L3)}
            if doc:
                files[owner_rel] = doc
            if doc and owner_rel.startswith("tasks/"):
                files[owner_rel] = with_decisions(doc, L1)
            before = FakeVault(files)
            after = FakeVault(migrate(files, owner_rel, legacy_rel))
            for caps in (Caps(), Caps(pack_chars=600)):
                a, b = render(before, kind, key, caps), render(after, kind, key, caps)
                self.assertTrue(a, (kind, key))
                self.assertEqual(a, b, (kind, key, caps.pack_chars))
            self.assertEqual(
                packs.pack_pointer(before, kind, key), packs.pack_pointer(after, kind, key)
            )
            self.assertIn(legacy_rel, packs.own_files(kind, key))


if __name__ == "__main__":
    unittest.main()
