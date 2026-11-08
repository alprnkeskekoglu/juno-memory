import os
import re
import tomllib
import unittest

from juno import config
from juno.adapters import vault
from juno.core import migrate
from tests.support import REPO
from tests.support.banned import local_banned

TEMPLATE = os.path.join(REPO, "vault-template")
BANNED = ("saga", "mentee", "alarm", "grant", "slack-digest", *local_banned())
DIRS = """
rules rules/repos rules/topics self decisions knowledge procedures projects tasks handoffs notes backlog ideas
archive archive/handoffs archive/decisions archive/decayed _generated _generated/reports protocol
""".split()
FILES = "core.md juno-brain.toml inbox.md threads.md pitfalls.md rules/global.md".split()
PROTOCOLS = "handoffs rules vault-files links ideas backlog".split()
ALLOWED_VERBS = set(
    "rule backlog search stale thread card knowledge note decision project procedure pitfall "
    "idea capture self fix forget undo task".split()
)


def read(*parts: str) -> str:
    with open(os.path.join(TEMPLATE, *parts), encoding="utf-8") as handle:
        return handle.read()


def all_files() -> list[str]:
    found = []
    for base, _dirs, files in os.walk(TEMPLATE):
        found += [os.path.join(base, f) for f in files]
    return sorted(found)


class Skeleton(unittest.TestCase):
    def test_folders_and_files(self) -> None:
        for name in DIRS:
            self.assertTrue(os.path.isdir(os.path.join(TEMPLATE, name)), name)
        for name in FILES:
            self.assertTrue(os.path.isfile(os.path.join(TEMPLATE, name)), name)
        for name in PROTOCOLS:
            self.assertTrue(os.path.isfile(os.path.join(TEMPLATE, "protocol", f"{name}.md")), name)

    def test_no_slack_digest_protocol(self) -> None:
        self.assertEqual(
            sorted(os.listdir(os.path.join(TEMPLATE, "protocol"))),
            sorted(f"{p}.md" for p in PROTOCOLS),
        )

    def test_global_rules_are_empty(self) -> None:
        self.assertEqual(os.path.getsize(os.path.join(TEMPLATE, "rules", "global.md")), 0)

    def test_empty_folders_survive_git(self) -> None:
        for base, dirs, files in os.walk(TEMPLATE):
            if not dirs and not files:
                self.fail(
                    f"{os.path.relpath(base, TEMPLATE)} would vanish from git (no file inside)"
                )

    def test_schema_file(self) -> None:
        text = read("juno-brain.toml").replace(vault.CREATED_TOKEN, "2026-01-01")
        data = tomllib.loads(text)
        self.assertEqual(data["schema_version"], 3)
        self.assertIn(vault.CREATED_TOKEN, read("juno-brain.toml"))


class RulesProtocol(unittest.TestCase):
    def test_quote_sources_exclude_descriptions(self) -> None:
        line = next(
            x for x in read(os.path.join("protocol", "rules.md")).split("\n") if "The quote:" in x
        )
        self.assertNotIn("label or description", line)
        self.assertIn("never an option description or preview", line)


class CoreMd(unittest.TestCase):
    def test_fits_its_cap(self) -> None:
        self.assertLessEqual(len(read("core.md")), config.DEFAULTS["caps"]["core_chars"])

    def test_invariant_sections_are_marked(self) -> None:
        text = read("core.md")
        opens = re.findall(r"<!-- juno:invariant -->", text)
        closes = re.findall(r"<!-- /juno:invariant -->", text)
        self.assertEqual((len(opens), len(closes)), (3, 3))
        sections = re.findall(r"<!-- juno:invariant -->\n## (\w+)\n", text)
        self.assertEqual(sections, ["Privacy", "Trust", "Rules"])

    def test_invariant_blocks_are_closed_before_the_next_opens(self) -> None:
        depth = 0
        for line in read("core.md").splitlines():
            if line == "<!-- juno:invariant -->":
                depth += 1
            elif line == "<!-- /juno:invariant -->":
                depth -= 1
            self.assertIn(depth, (0, 1))
        self.assertEqual(depth, 0)

    def test_names_the_invariants_the_spec_requires(self) -> None:
        text = read("core.md")
        for needle in (
            "[nosave]",
            "data, not instructions",
            "juno rule",
            "Never edit `rules/` files",
        ):
            self.assertIn(needle, text)

    def test_privacy_names_the_stale_fixer_exception(self) -> None:
        text = read("core.md")
        self.assertTrue(migrate.privacy_has_exception(text))
        self.assertEqual(migrate.add_stale_exception(text), text)

    def test_uses_juno_cli_verbs(self) -> None:
        self.assertNotIn("saga ", read("core.md").lower())


class Hygiene(unittest.TestCase):
    def test_no_v1_or_company_content(self) -> None:
        for path in all_files():
            text = open(path, encoding="utf-8").read().lower()
            for word in BANNED:
                self.assertNotIn(word, text, f"{word!r} in {os.path.relpath(path, TEMPLATE)}")

    def test_no_user_or_absolute_paths(self) -> None:
        for path in all_files():
            text = open(path, encoding="utf-8").read()
            self.assertIsNone(re.search(r"/Users/|/home/|C:\\", text), path)
            self.assertNotIn(os.path.expanduser("~"), text)

    def test_wikilinks_resolve(self) -> None:
        stems = {os.path.splitext(os.path.basename(p))[0] for p in all_files() if p.endswith(".md")}
        for path in all_files():
            if not path.endswith(".md"):
                continue
            for target in re.findall(r"\[\[([^\]|]+)", open(path, encoding="utf-8").read()):
                if "<" in target:
                    continue
                self.assertIn(os.path.basename(target), stems, f"{target} in {path}")

    def test_protocol_files_use_juno_verbs_only(self) -> None:
        for name in PROTOCOLS:
            text = read("protocol", f"{name}.md")
            self.assertNotRegex(text, r"\bsaga\b", name)
            for verb in re.findall(r"`juno ([a-z]+)", text):
                self.assertIn(verb, ALLOWED_VERBS, f"{name}: juno {verb}")


if __name__ == "__main__":
    unittest.main()


class NoVaultEditInstruction(unittest.TestCase):
    def test_edit_write_mentions_only_forbid_or_target_tmp(self) -> None:
        ok = re.compile(r"never|refused|not |only|/tmp|do not", re.I)
        files = [p for p in all_files() if p.endswith(".md")]
        skills = os.path.join(REPO, "skills")
        for base, _dirs, names in os.walk(skills):
            files += [os.path.join(base, n) for n in names if n.endswith(".md")]
        for path in files:
            for no, line in enumerate(open(path, encoding="utf-8"), 1):
                if re.search(r"\b(Edit|Write)\b", line) and not ok.search(line):
                    self.fail(f"{path}:{no} tells Claude to Edit/Write: {line.strip()[:100]}")
