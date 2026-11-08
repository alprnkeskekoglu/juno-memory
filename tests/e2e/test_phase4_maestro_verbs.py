import os

from tests.e2e.harness import JUNO_BIN
from tests.e2e.test_phase2_cli import Case, command
from tests.support import e2e

EVIDENCE = "the alpha service runs its tests with phpunit inside the container"
BLOCK = (
    "runtime:\n  project: alpha\n  lang: php\n  runner: {argv: [vendor/bin/phpunit], where: host}\n"
)


@e2e
class ProjectRuntimeBlock(Case):
    def test_runtime_block_is_written_into_the_profile_frontmatter(self) -> None:
        os.makedirs(self.sb.state, exist_ok=True)
        path = os.path.join(self.sb.state, "runtime-block.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(BLOCK)
        self.put("projects/alpha.md", "---\nrepo: alpha\nupdated: 2026-10-01\n---\n\n## Gotchas\n")
        run = self.go(
            f"Fact: {EVIDENCE}. "
            + command(
                JUNO_BIN,
                "project runtime --repo alpha --evidence",
                f'"{EVIDENCE}"',
                f"--block-file {path}",
            )
        )
        self.assertIn(
            "runtime block written to projects/alpha.md", self.output(run), run.raw[-2500:]
        )
        text = self.get("projects/alpha.md")
        self.assertIn(BLOCK + "---\n", text)
        self.assertTrue(text.startswith("---\nrepo: alpha\nupdated: "))
        self.assertIn("## Gotchas", text)


@e2e
class ProjectLinksLine(Case):
    def test_links_line_is_added_to_the_links_section(self) -> None:
        self.put("knowledge/cache.md", "---\nupdated: 2026-10-01\n---\nThe TTL is 60 seconds.\n")
        run = self.go(
            f"Fact: {EVIDENCE}. "
            + command(
                JUNO_BIN,
                "project links --repo alpha --evidence",
                f'"{EVIDENCE}"',
                '"[[knowledge/cache]] cache facts for alpha"',
            )
        )
        self.assertIn(
            "profile line written to projects/alpha.md", self.output(run), run.raw[-2500:]
        )
        self.assertIn(
            "- [[knowledge/cache]] cache facts for alpha · ", self.get("projects/alpha.md")
        )


@e2e
class TaskSlackChecked(Case):
    def test_task_set_records_the_slack_check_day(self) -> None:
        with open(self.sb.config, "a", encoding="utf-8") as handle:
            handle.write('\n[jira]\nprefixes = ["DEF"]\n')
        run = self.go(
            command(JUNO_BIN, "task set DEF-7 --title 'Add widget' --status open")
            + " Then "
            + command(JUNO_BIN, "task set DEF-7 --slack-checked 2026-11-07")
        )
        text = self.get("tasks/DEF-7.md")
        self.assertIn("slack_checked: 2026-11-07", text, run.raw[-2500:])
        self.assertIn("title: Add widget", text)
