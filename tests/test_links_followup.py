import os
import re

from juno.core import cardfmt, migrate
from tests.support import REPO, SandboxCase
from tests.test_link_verbs import TEXT, LinkBase

OLD_FORMAT = "Links: [[<repo>]], [[<KEY>]], ..."
OLD_FRAGMENT = '[--links "[[<repo>]]"]'
NEW_FORMAT = "Links: [[<repo>]] (why), ..."
NEW_FRAGMENT = '[--links "[[<repo>]] (why)"]'
REASON = "profile with the run steps"


def template_core() -> str:
    with open(os.path.join(REPO, "vault-template", "core.md"), encoding="utf-8") as handle:
        return handle.read()


class TemplateText(SandboxCase):
    def test_core_shows_only_reasoned_links_and_stays_under_the_warn_line(self) -> None:
        core = template_core()
        self.assertNotIn(OLD_FORMAT, core)
        self.assertNotIn(OLD_FRAGMENT, core)
        self.assertIn(NEW_FORMAT, core)
        self.assertIn(NEW_FRAGMENT, core)
        self.assertLess(len(core.strip()), 3600)

    def test_prompt_from_an_unmigrated_core_still_asks_for_parenthesised_reasons(self) -> None:
        old = template_core().replace(NEW_FORMAT, OLD_FORMAT)
        text = cardfmt.format_block(old)
        self.assertIn(OLD_FORMAT, text)
        prompt = cardfmt.build_prompt(
            name="n",
            seg8="abcd1234",
            key="-",
            repos=[],
            tickets=[],
            format_text=text,
            stamp="2026-01-01 00:00",
            digest="d",
            fence="f",
            naming=None,
            extract_lines=[],
            state_text="",
        )
        self.assertRegex(prompt, r"reason in parentheses right after (each|every) link")
        self.assertIn("`Links: -`", prompt)
        self.assertRegex(prompt, r"even where the format")


class CardVerb(LinkBase):
    def card(self, links: str) -> tuple[int, str]:
        return self.juno(
            "card",
            "--label",
            "worker",
            "--learned",
            "none",
            "--links",
            links,
            "Fixed the worker restart.",
            "Next: open the pull request.",
        )

    def test_a_reasonless_link_is_refused_with_the_form(self) -> None:
        code, out = self.card("[[alpha]]")
        self.assertEqual(code, 2, out)
        self.assertIn("[[alpha]]", out)
        self.assertIn('--links "[[repo]] (why this card relies on it)"', out)

    def test_a_reasoned_link_is_kept(self) -> None:
        code, out = self.card(f"[[alpha]] ({REASON})")
        self.assertEqual(code, 0, out)
        names = [n for n in os.listdir(self.vp("handoffs")) if n.endswith(".md")]
        text = "".join(self.get(f"handoffs/{n}") for n in names)
        self.assertIn(f"Links: [[alpha]] ({REASON})", text)


class NoteDuplicate(LinkBase):
    def test_links_go_to_the_existing_note(self) -> None:
        self.write_note()
        out = self.ok("note", "--slug", "other-name", TEXT.lower(), "--link", f"alpha:{REASON}")
        self.assertEqual(len(self.notes()), 1)
        self.assertRegex(out, r"write w-[0-9a-f]{8}")
        self.assertIn("links", out)
        self.assertEqual(
            self.get(self.note_path()).splitlines()[-1], f"Links: [[alpha]] ({REASON})"
        )

    def test_without_a_link_nothing_changes(self) -> None:
        self.write_note()
        before = self.get(self.note_path())
        out = self.ok("note", "--slug", "other-name", TEXT.lower())
        self.assertIn("already recorded", out)
        self.assertEqual(self.get(self.note_path()), before)


class Step4To5(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)
        self.toml = os.path.join(self.sb.vault, "juno-brain.toml")
        self.core = os.path.join(self.sb.vault, "core.md")

    def schema4(self, core: str) -> None:
        with open(self.core, "w", encoding="utf-8") as handle:
            handle.write(core)
        with open(self.toml, encoding="utf-8") as handle:
            text = handle.read()
        with open(self.toml, "w", encoding="utf-8") as handle:
            handle.write(migrate.write_schema(text, 4))

    def read(self) -> str:
        with open(self.core, encoding="utf-8") as handle:
            return handle.read()

    def schema(self) -> int | None:
        with open(self.toml, encoding="utf-8") as handle:
            return migrate.read_schema(handle.read())

    def old_core(self) -> str:
        return template_core().replace(NEW_FORMAT, OLD_FORMAT).replace(NEW_FRAGMENT, OLD_FRAGMENT)

    def test_both_strings_are_rewritten_once(self) -> None:
        self.schema4(self.old_core())
        code, out = self.sb.cli("migrate")
        self.assertEqual(code, 0, out)
        self.assertIn("schema 4 -> 5", out)
        self.assertEqual(self.schema(), 5)
        self.assertEqual(self.read(), template_core())
        self.assertEqual(self.sb.cli("migrate")[0], 0)
        self.assertEqual(self.read(), template_core())

    def test_only_the_format_line_present(self) -> None:
        core = re.sub(r" \[--links \"[^\n]*?\]\]\"\]", "", self.old_core(), count=1)
        self.assertNotIn("--links", core)
        self.schema4(core)
        self.assertEqual(self.sb.cli("migrate")[0], 0)
        self.assertEqual(self.read(), core.replace(OLD_FORMAT, NEW_FORMAT))

    def test_only_the_command_fragment_present(self) -> None:
        core = self.old_core().replace(OLD_FORMAT, "Links: [[x]] (mine)")
        self.schema4(core)
        self.assertEqual(self.sb.cli("migrate")[0], 0)
        self.assertEqual(self.read(), core.replace(OLD_FRAGMENT, NEW_FRAGMENT))

    def test_an_edited_line_is_left_alone(self) -> None:
        core = self.old_core().replace(OLD_FORMAT, "Links: [[<repo>]], mine")
        self.schema4(core)
        self.assertEqual(self.sb.cli("migrate")[0], 0)
        self.assertEqual(self.schema(), 5)
        self.assertIn("Links: [[<repo>]], mine", self.read())
        self.assertIn(NEW_FRAGMENT, self.read())
