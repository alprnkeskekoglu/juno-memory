import json
import os
import re
import unittest
from unittest import mock

from juno.cmd import init
from tests.support import REPO, SandboxCase

STYLE_DIR = os.path.join(REPO, "output-styles")
NAMES = ("juno-calm", "juno-normal", "juno-playful")
DOSE_MARK = "Humour dose:"
QUESTION = "Juno style:"


def frontmatter(text: str) -> dict[str, str]:
    block = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert block is not None
    return dict(line.split(": ", 1) for line in block.group(1).splitlines())


def read_style(name: str) -> tuple[str, list[str]]:
    with open(os.path.join(STYLE_DIR, f"{name}.md"), encoding="utf-8") as handle:
        text = handle.read()
    return text, text.split("---\n", 2)[2].strip().splitlines()


class StyleFiles(unittest.TestCase):
    def test_frontmatter_keeps_coding_instructions_and_is_not_forced(self) -> None:
        for name in NAMES:
            with self.subTest(name=name):
                meta = frontmatter(read_style(name)[0])
                self.assertEqual(meta["name"], name)
                self.assertTrue(meta["description"])
                self.assertEqual(meta["keep-coding-instructions"], "true")
                self.assertNotIn("force-for-plugin", meta)

    def test_init_choices_are_the_three_files(self) -> None:
        self.assertEqual(init.OUTPUT_STYLES, tuple(f"juno:{n}" for n in NAMES))

    def test_files_share_one_character_block_and_differ_by_the_dose_block(self) -> None:
        def split(name: str) -> tuple[str, str]:
            body = "\n".join(read_style(name)[1])
            head, mark, tail = body.partition(DOSE_MARK)
            self.assertEqual(mark, DOSE_MARK)
            return head, tail

        characters = {split(n)[0] for n in NAMES}
        self.assertEqual(len(characters), 1)
        self.assertEqual(len({split(n)[1] for n in NAMES}), 3)
        for name in NAMES:
            self.assertIn(f"{DOSE_MARK} {name.split('-')[1]}.", read_style(name)[0])

    def test_character_carries_every_trait_and_the_two_rules(self) -> None:
        text = read_style(NAMES[0])[0].lower()
        for needle in (
            "close friend",
            "answer first",
            "plainly that it is wrong",
            "rarely ask",
            "structured question tool",
            "never other people or their code",
            "dig at yourself",
            "lowercase",
            "emoji",
            "greet",
            "never invent",
            "no jokes, no teasing and no emoji at any dose",
            "calm the user first",
            "every language",
            "name it",
            "keep apart",
            "never claim something is done without evidence",
            "claude.md",
            "run on claude",
            "user's own voice",
            "own format",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_text_names_no_company_person_or_plugin(self) -> None:
        for name in NAMES:
            text = read_style(name)[0].lower()
            for word in ("acme", "globex", "maestro", "jira", "slack", "turkish", "/users/", "@"):
                with self.subTest(name=name, word=word):
                    self.assertNotIn(word, text)

    def test_core_says_who_it_is(self) -> None:
        body = read_style(NAMES[0])[1]
        self.assertIn("You are Juno", body[0])
        self.assertIn("run on Claude", body[-2])

    def test_every_dose_keeps_serious_moments_joke_free(self) -> None:
        for name in NAMES:
            self.assertIn("Serious moments have no jokes", read_style(name)[1][-1])

    def test_retired_names_map_unambiguously(self) -> None:
        self.assertEqual(
            init.RETIRED_STYLES,
            {
                "juno:juno": "juno:juno-calm",
                "juno:juno-witty": "juno:juno-normal",
                "juno:juno-warm": "juno:juno-normal",
                "juno:juno-mentor": "juno:juno-normal",
            },
        )
        self.assertFalse(set(init.RETIRED_STYLES) & set(init.OUTPUT_STYLES))

    def test_files_end_with_a_newline(self) -> None:
        for name in NAMES:
            with self.subTest(name=name):
                self.assertTrue(read_style(name)[0].endswith("\n"))


class Offer(SandboxCase):
    def init(self, answer: str | None, tty: bool = True) -> tuple[int, str, list[str]]:
        asked: list[str] = []

        def fake_input(question: str) -> str:
            asked.append(question)
            if answer is None:
                raise EOFError
            return answer if QUESTION in question else "n"

        with (
            mock.patch.object(init, "interactive", return_value=tty),
            mock.patch("builtins.input", fake_input),
        ):
            code, out = self.sb.cli("init", *self.sb.install_args())
        return code, out, [q for q in asked if QUESTION in q]

    def style(self) -> object:
        return json.loads(self.sb.read_settings() or b"{}").get("outputStyle")

    def test_choice_sets_the_style_and_asks_for_a_restart(self) -> None:
        self.sb.write_settings(b'{\n    "model": "opus"\n}\n')
        code, out, asked = self.init("1")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(asked), 1)
        self.assertIn("[4]", asked[0])
        for word in ("calm", "normal", "playful", "none"):
            self.assertIn(word, asked[0])
        self.assertEqual(self.style(), "juno:juno-calm")
        self.assertIn("restart Claude Code", out)
        raw = (self.sb.read_settings() or b"").decode()
        self.assertIn('    "model": "opus"', raw)
        self.assertTrue(raw.endswith("\n"))
        backups = os.listdir(os.path.join(self.sb.state, "install-backups"))
        self.assertEqual(len(backups), 2)

    def test_each_number_maps_to_its_style(self) -> None:
        for number, value in init.STYLE_CHOICES.items():
            with self.subTest(number=number):
                self.setUp()
                self.init(number)
                self.assertEqual(self.style(), value)

    def test_a_choice_without_a_settings_file_creates_it(self) -> None:
        self.init("2")
        self.assertEqual(self.style(), "juno:juno-normal")

    def test_four_empty_and_invalid_answers_leave_the_settings_alone(self) -> None:
        for answer in ("4", "", "maybe", "0", "5", "y"):
            with self.subTest(answer=answer):
                self.setUp()
                code, out, asked = self.init(answer)
                self.assertEqual(code, 0, out)
                self.assertEqual(len(asked), 1)
                self.assertIsNone(self.style())
                self.assertIn("output style: left off", out)

    def test_eof_leaves_the_settings_alone(self) -> None:
        self.init(None)
        self.assertIsNone(self.style())

    def test_non_interactive_asks_nothing(self) -> None:
        code, out, asked = self.init("2", tty=False)
        self.assertEqual((code, asked), (0, []))
        self.assertIsNone(self.style())
        self.assertNotIn("output style", out)

    def test_dry_run_asks_nothing(self) -> None:
        with (
            mock.patch.object(init, "interactive", return_value=True),
            mock.patch("builtins.input", side_effect=AssertionError("asked")),
        ):
            code, _ = self.sb.cli("init", *self.sb.install_args(), "--dry-run")
        self.assertEqual(code, 0)
        self.assertIsNone(self.sb.read_settings())

    def test_asked_once(self) -> None:
        self.init("4")
        code, _, asked = self.init("1")
        self.assertEqual((code, asked), (0, []))
        self.assertIsNone(self.style())

    def test_another_style_is_printed_and_kept(self) -> None:
        raw = b'{"outputStyle": "Explanatory"}\n'
        self.sb.write_settings(raw)
        code, out, asked = self.init("1")
        self.assertEqual((code, asked), (0, []))
        self.assertIn("'Explanatory'", out)
        self.assertIn("settings: merged", out)
        self.assertEqual(self.style(), "Explanatory")
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, init.STYLE_MARKER)))

    def test_a_juno_style_already_set_is_not_asked_again(self) -> None:
        for value in init.OUTPUT_STYLES:
            with self.subTest(value=value):
                self.setUp()
                self.sb.write_settings(json.dumps({"outputStyle": value}).encode())
                code, out, asked = self.init("1")
                self.assertEqual((code, asked), (0, []))
                self.assertIn(f"{value} already set", out)

    def test_rerunning_init_keeps_the_record_and_uninstall_removes_the_style(self) -> None:
        for number, value in init.STYLE_CHOICES.items():
            with self.subTest(value=value):
                self.setUp()
                original = b'{\n  "model": "opus"\n}\n'
                self.sb.write_settings(original)
                self.init(number)
                self.sb.cli("init", *self.sb.install_args())
                self.assertEqual(self.style(), value)
                code, out = self.sb.cli("uninstall", *self.sb.install_args())
                self.assertEqual(code, 0, out)
                self.assertEqual(self.sb.read_settings(), original)

    def test_uninstall_after_a_later_edit_removes_only_the_recorded_style(self) -> None:
        self.sb.write_settings(b'{"model": "opus"}\n')
        self.init("3")
        data = json.loads(self.sb.read_settings() or b"")
        data["theme"] = "dark"
        self.sb.write_settings(json.dumps(data).encode())
        self.sb.cli("uninstall", *self.sb.install_args())
        left = json.loads(self.sb.read_settings() or b"")
        self.assertEqual(left, {"model": "opus", "theme": "dark"})

    def test_uninstall_leaves_a_style_the_user_changed(self) -> None:
        self.sb.write_settings(b"{}\n")
        self.init("2")
        data = json.loads(self.sb.read_settings() or b"")
        data["outputStyle"] = "Learning"
        self.sb.write_settings(json.dumps(data).encode())
        _, out = self.sb.cli("uninstall", *self.sb.install_args())
        self.assertIn("outputStyle changed since install", out)
        self.assertEqual(self.style(), "Learning")


class Doctor(SandboxCase):
    def line(self) -> tuple[str, str]:
        _, out = self.sb.cli("doctor", "--json", *self.sb.install_args())
        found = [c for c in json.loads(out)["checks"] if c["name"] == "output style"]
        self.assertEqual(len(found), 1, out)
        return found[0]["status"], found[0]["detail"]

    def select(self, value: str) -> None:
        self.sb.cli("init", *self.sb.install_args())
        data = json.loads(self.sb.read_settings() or b"")
        data["outputStyle"] = value
        self.sb.write_settings(json.dumps(data).encode())

    def test_info_when_not_active(self) -> None:
        self.sb.cli("init", *self.sb.install_args())
        status, detail = self.line()
        self.assertEqual(status, "INFO")
        self.assertIn("juno:juno-calm", detail)

    def test_info_for_another_style(self) -> None:
        self.select("Explanatory")
        self.assertEqual(self.line()[0], "INFO")

    def test_pass_for_each_juno_style_and_shows_which(self) -> None:
        for value in init.OUTPUT_STYLES:
            with self.subTest(value=value):
                self.setUp()
                self.select(value)
                status, detail = self.line()
                self.assertEqual(status, "PASS")
                self.assertIn(value, detail)

    def test_warn_when_the_selected_style_file_is_missing_from_the_plugin(self) -> None:
        self.select("juno:juno-playful")
        with mock.patch("os.path.isfile", lambda p: not p.endswith("juno-playful.md")):
            status, detail = self.line()
        self.assertEqual(status, "WARN")
        self.assertIn("juno-playful.md", detail)

    def test_warn_when_the_first_file_is_missing_and_none_is_selected(self) -> None:
        self.sb.cli("init", *self.sb.install_args())
        with mock.patch("os.path.isfile", lambda p: not p.endswith("juno-calm.md")):
            self.assertEqual(self.line()[0], "WARN")

    def test_warn_for_a_retired_name_with_the_fixing_command(self) -> None:
        self.select("juno:juno-warm")
        status, detail = self.line()
        self.assertEqual(status, "WARN")
        self.assertIn("juno:juno-warm", detail)
        self.assertIn("juno migrate --style", detail)
        self.assertIn("juno:juno-normal", detail)


class MigrateStyle(SandboxCase):
    def migrate(self, *extra: str) -> tuple[int, str]:
        return self.sb.cli("migrate", "--style", *extra, *self.sb.install_args())

    def style(self) -> object:
        return json.loads(self.sb.read_settings() or b"{}").get("outputStyle")

    def test_each_retired_value_migrates_and_doctor_is_clean(self) -> None:
        for old, new in init.RETIRED_STYLES.items():
            with self.subTest(old=old):
                self.setUp()
                self.sb.cli("init", *self.sb.install_args())
                data = json.loads(self.sb.read_settings() or b"")
                data["outputStyle"] = old
                data["model"] = "opus"
                self.sb.write_settings((json.dumps(data, indent=2) + "\n").encode())
                code, out = self.migrate()
                self.assertEqual(code, 0, out)
                self.assertEqual(self.style(), new)
                self.assertIn(new, out)
                left = json.loads(self.sb.read_settings() or b"")
                self.assertEqual(left["model"], "opus")
                backups = os.listdir(os.path.join(self.sb.state, "install-backups"))
                self.assertTrue(any(b.startswith("settings-") for b in backups))
                _, doctor = self.sb.cli("doctor", "--json", *self.sb.install_args())
                found = [c for c in json.loads(doctor)["checks"] if c["name"] == "output style"]
                self.assertEqual(found[0]["status"], "PASS", found)

    def test_dry_run_changes_nothing(self) -> None:
        raw = b'{"outputStyle": "juno:juno-warm"}\n'
        self.sb.write_settings(raw)
        code, out = self.migrate("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("would", out)
        self.assertEqual(self.sb.read_settings(), raw)

    def test_other_values_are_left_alone(self) -> None:
        for value in ("Explanatory", "juno:juno-playful"):
            with self.subTest(value=value):
                raw = json.dumps({"outputStyle": value}).encode()
                self.sb.write_settings(raw)
                code, out = self.migrate()
                self.assertEqual(code, 0, out)
                self.assertEqual(self.sb.read_settings(), raw)
                self.assertIn("nothing to migrate", out)

    def test_recorded_style_follows_so_uninstall_still_removes_it(self) -> None:
        self.sb.write_settings(b"{}\n")
        self.sb.cli("init", *self.sb.install_args())
        data = json.loads(self.sb.read_settings() or b"")
        data["outputStyle"] = "juno:juno-witty"
        self.sb.write_settings(json.dumps(data).encode())
        manifest = os.path.join(self.sb.state, "install-manifest.json")
        with open(manifest, encoding="utf-8") as handle:
            record = json.load(handle)
        record["record"]["outputStyle"] = "juno:juno-witty"
        with open(manifest, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        self.migrate()
        self.sb.cli("uninstall", *self.sb.install_args())
        self.assertIsNone(self.style())


if __name__ == "__main__":
    unittest.main()
