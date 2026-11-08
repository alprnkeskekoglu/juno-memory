import json
import os

from tests.test_writegate_cli import Base

BLOCK = (
    "runtime:\n  project: app\n  lang: php\n  runner: {argv: [vendor/bin/phpunit], where: host}\n"
)
EV = ("--repo", "app", "--evidence", "repo:app")


class Runtime(Base):
    def setUp(self) -> None:
        super().setUp()
        self.touch_repo("app")

    def write(self, block: str) -> str:
        return self.ok("project", "runtime", *EV, "--block-file", self.body_file(block))

    def test_creates_a_profile_and_the_body_file_is_consumed(self) -> None:
        path = self.body_file(BLOCK)
        self.ok("project", "runtime", *EV, "--block-file", path)
        text = self.get("projects/app.md")
        self.assertTrue(text.startswith("---\nrepo: app\nupdated: "))
        self.assertIn(BLOCK.strip("\n"), text)
        self.assertIn("## Links", text)
        self.assertFalse(os.path.exists(path))

    def test_replaces_only_the_runtime_block_and_undo_restores(self) -> None:
        self.put(
            "projects/app.md",
            "---\nrepo: app\nstack: php\nruntime:\n  project: old\n  lang: go\n"
            "updated: 2026-10-01\n---\n\n## Gotchas\n\n- keep me\n",
        )
        out = self.write(BLOCK)
        text = self.get("projects/app.md")
        self.assertIn("stack: php\nruntime:\n  project: app\n  lang: php\n  runner:", text)
        self.assertNotIn("project: old", text)
        self.assertIn("- keep me", text)
        self.assertEqual(text.count("runtime:"), 1)
        self.ok("undo", self.wid(out))
        self.assertIn("project: old", self.get("projects/app.md"))

    def test_refuses_bad_blocks(self) -> None:
        for bad in (
            "lang: php\n",
            "runtime:\n",
            "runtime:\n  a: 1\n  a: 2\n",
            "runtime:\nlang: x\n",
        ):
            self.refused("project", "runtime", *EV, "--block-file", self.body_file(bad))
        secret = "runtime:\n  caveats: ghp_" + "a1B2c3D4e5F6" * 3 + "\n"
        self.refused("project", "runtime", *EV, "--block-file", self.body_file(secret))
        self.refused("project", "runtime", *EV)
        self.assertEqual(self.get("projects/app.md"), "")


class Links(Base):
    def setUp(self) -> None:
        super().setUp()
        self.touch_repo("app")
        self.put("knowledge/cache.md", "---\nupdated: 2026-10-01\n---\nTTL 60s.\n")

    def test_adds_a_link_line_to_the_links_section(self) -> None:
        self.ok("project", "links", *EV, "[[knowledge/cache]] cache facts for this service")
        text = self.get("projects/app.md")
        self.assertIn("## Links\n\n- [[knowledge/cache]] cache facts for this service · ", text)

    def test_refuses_missing_target_and_bad_shape(self) -> None:
        self.refused("project", "links", *EV, "[[knowledge/none]] a missing file")
        self.refused("project", "links", *EV, "knowledge/cache without brackets")
        self.refused("project", "links", *EV, "[[knowledge/cache]]x")


class SlackChecked(Base):
    TICKETS = True

    def test_task_set_slack_checked(self) -> None:
        self.ok("task", "set", "ABC-5", "--title", "Fix it", "--status", "open")
        self.ok("task", "set", "ABC-5", "--slack-checked", "2026-11-07")
        self.assertEqual(self.get("tasks/ABC-5.md").count("slack_checked: 2026-11-07"), 1)
        self.ok("task", "set", "ABC-5", "--slack-checked", "2026-11-08")
        self.assertEqual(self.get("tasks/ABC-5.md").count("slack_checked:"), 1)
        row = json.loads(self.ok("task", "show", "ABC-5", "--json"))
        self.assertEqual(row["key"], "ABC-5")
        code, out = self.juno("task", "set", "ABC-5", "--slack-checked", "yesterday")
        self.assertEqual(code, 2, out)
