import os
import re
import unittest

from juno.core.vaultfmt import parse_frontmatter
from tests.support import REPO
from tests.support.banned import local_banned

SKILLS = ("juno", "backlog")
BANNED = ("saga", "alarm", "mentee", "/Users/", "/opt/homebrew", *local_banned())


def read(name: str) -> str:
    with open(os.path.join(REPO, "skills", name, "SKILL.md"), encoding="utf-8") as handle:
        return handle.read()


class SkillFiles(unittest.TestCase):
    def test_frontmatter_names_match_the_folders(self) -> None:
        for name in SKILLS:
            with self.subTest(name):
                fields, body = parse_frontmatter(read(name))
                self.assertEqual(fields["name"], name)
                self.assertTrue(str(fields["description"]).strip())
                self.assertTrue(body.strip())

    def test_allowed_tools_only_name_juno_commands(self) -> None:
        for name in SKILLS:
            fields, _ = parse_frontmatter(read(name))
            bash = re.findall(r"Bash\(([^)]*)\)", str(fields["allowed-tools"]))
            self.assertTrue(bash)
            self.assertTrue(all(x.startswith("juno ") for x in bash), bash)

    def test_no_v1_names_or_user_paths(self) -> None:
        for name in SKILLS:
            text = read(name).casefold()
            for banned in BANNED:
                self.assertNotIn(banned.casefold(), text, f"{name}: {banned}")

    def test_juno_skill_is_read_only(self) -> None:
        fields, _ = parse_frontmatter(read("juno"))
        tools = str(fields["allowed-tools"])
        for verb in ("rule", "snapshot", "compact", "digest", "backlog"):
            self.assertNotIn(f"juno {verb}", tools)

    def test_juno_skill_allows_vault_reads_and_caps_the_flow(self) -> None:
        fields, body = parse_frontmatter(read("juno"))
        tools = str(fields["allowed-tools"]).split()
        for tool in ("Read", "Grep", "Glob"):
            self.assertIn(tool, tools)
        for tool in ("Write", "Edit", "NotebookEdit"):
            self.assertNotIn(tool, tools)
        self.assertIn("at most 6 tool calls", body)
        self.assertIn("for reading the vault only", body)

    def test_every_verb_the_skills_name_exists_or_is_planned(self) -> None:
        from juno import cli

        known = set(cli.VERBS) | {"search", "recap", "stale", "profiles"}
        for name in SKILLS:
            for verb in re.findall(r"`juno ([a-z]+)", read(name)):
                self.assertIn(verb, known, f"{name}: juno {verb}")


def frontmatter_files() -> list[str]:
    found = []
    for folder, name in (("skills", "SKILL.md"), ("agents", None), ("output-styles", None)):
        base = os.path.join(REPO, folder)
        for entry in sorted(os.listdir(base)):
            path = os.path.join(base, entry, name) if name else os.path.join(base, entry)
            if os.path.isfile(path) and path.endswith(".md"):
                found.append(path)
    return found


class FrontmatterIsStrictYaml(unittest.TestCase):
    """A plain YAML scalar may not hold ": " or " #", nor start with an indicator; a quoted one
    must close. Strict readers truncate or reject the file otherwise."""

    INDICATORS = "[]{},&*!|>%@`#?-:"

    def test_every_value_is_valid_as_written(self) -> None:
        paths = frontmatter_files()
        self.assertTrue(paths)
        for path in paths:
            with open(path, encoding="utf-8") as handle:
                lines = handle.read().split("\n")
            end = lines.index("---", 1)
            for line in lines[1:end]:
                match = re.match(r"^([A-Za-z_][\w-]*):[ \t]+(.*?)\s*$", line)
                if not match:
                    continue
                key, value = match.groups()
                where = f"{os.path.relpath(path, REPO)}: {key}"
                if value[:1] in "\"'":
                    self.assertTrue(
                        len(value) >= 2 and value[-1] == value[0], f"{where}: unclosed quote"
                    )
                    continue
                self.assertNotIn(": ", value, where)
                self.assertNotIn(" #", value, where)
                self.assertNotIn(value[:1], self.INDICATORS, f"{where}: starts with indicator")


class VoiceImportSkill(unittest.TestCase):
    """Text-level checks; the behaviour itself (criteria 15 and 16) needs a live session."""

    def setUp(self) -> None:
        self.fields, self.body = parse_frontmatter(read("voice-import"))

    def test_exists_and_is_registered(self) -> None:
        self.assertEqual(self.fields["name"], "voice-import")
        self.assertTrue(self.body.strip())
        with open(os.path.join(REPO, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        self.assertIn("/juno:voice-import", readme)
        self.assertTrue(os.path.isdir(os.path.join(REPO, "skills", "voice-import")))

    def test_description_restricts_invocation_to_the_user(self) -> None:
        text = str(self.fields["description"])
        self.assertIn("Use only when the user types /juno:voice-import", text)
        self.assertIn("in their own words", text)
        self.assertIn("Never on Claude's own initiative", text)

    def test_no_v1_names_or_user_paths(self) -> None:
        text = self.body.casefold() + str(self.fields["description"]).casefold()
        for banned in BANNED:
            self.assertNotIn(banned.casefold(), text, banned)

    def test_file_and_paste_always_offered_code_review_needs_gh(self) -> None:
        body = self.body
        self.assertIn("- **File**: always offered", body)
        self.assertIn("- **Paste**: always offered", body)
        self.assertIn("only File and Paste exist", body)
        self.assertIn("`command -v gh`", body)
        self.assertIn("`gh auth status`", body)
        self.assertIn("Offer it only when both succeed", body)
        self.assertIn("ONE AskUserQuestion with a multi-select", body)
        for source in ("Chat", "Code review", "Mail", "Tracker"):
            self.assertIn(f"- **{source}**", body)

    def test_only_the_users_own_messages_and_nothing_posted(self) -> None:
        self.assertIn("Only the user's own messages", self.body)
        self.assertIn("Never post", self.body)
        self.assertIn("commenter:@me", self.body)
        self.assertIn("gh api user -q .login", self.body)

    def test_dry_run_comes_before_import(self) -> None:
        body = self.body
        dry = body.index("juno voice import --dry-run")
        real = body.index("`juno voice import [--register")
        self.assertLess(dry, real)
        self.assertIn("Only after the user said yes", body)
        self.assertIn("juno voice forget --batch <id>", body)

    def test_message_text_is_never_printed(self) -> None:
        self.assertIn("Never print message text in the chat", self.body)
        self.assertIn("no message text", self.body)

    def test_temp_file_is_0600_outside_the_vault_and_deleted(self) -> None:
        body = self.body
        self.assertIn("install -m 600 /dev/null", body)
        self.assertIn("chmod 600", body)
        self.assertIn("Never inside the vault or the repo", body)
        self.assertIn("rm -f", body)
        self.assertLess(body.index("install -m 600"), body.index("## Step 5"))
        self.assertLess(body.index("## Step 6"), body.index("## Step 7"))

    def test_uses_the_exact_cli_shape(self) -> None:
        body = self.body
        self.assertIn(
            "juno voice import --dry-run [--register voice|formal]"
            " [--target chat|mail|tracker|review|code] <file>",
            body,
        )
        for flag in ("--json", "--batch"):
            self.assertIn(flag, body)
        tools = str(self.fields["allowed-tools"])
        self.assertIn("Bash(juno voice *)", tools)
        self.assertNotIn("Edit", tools.split())


if __name__ == "__main__":
    unittest.main()
