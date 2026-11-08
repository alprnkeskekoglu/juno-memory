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


if __name__ == "__main__":
    unittest.main()
