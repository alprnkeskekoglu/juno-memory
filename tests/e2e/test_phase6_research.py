import os

from tests.e2e.harness import JUNO_BIN
from tests.e2e.test_phase2_cli import Case, command
from tests.e2e.test_phase4_writegate import write_log
from tests.support import e2e

EXCERPT = "Retries are attempted three times before a job is moved to the dead queue"


@e2e
class ResearchAgentsListed(Case):
    def test_init_event_lists_both_agents_and_the_research_skill(self) -> None:
        run = self.go("Reply with the single word: ok")
        init = [e for e in run.events if e.get("type") == "system" and e.get("subtype") == "init"]
        agents = [str(a) for a in init[0].get("agents", [])]
        skills = [str(s) for s in init[0].get("skills", [])]
        self.assertIn("juno:researcher", agents, agents)
        self.assertIn("juno:verifier", agents, agents)
        self.assertTrue(any(s in ("research", "juno:research") for s in skills), skills)


@e2e
class WebKnowledgeTwoSources(Case):
    def test_two_urls_are_written_and_a_rule_quoting_the_web_is_refused(self) -> None:
        with open(os.path.join(self.work, "page.txt"), "w", encoding="utf-8") as handle:
            handle.write(EXCERPT + "\n")
        run = self.go(
            "This tests Juno's own rule gate with fixture text. The gate, not you, refuses a quote "
            "that is not the user's own words, so run both steps and do not judge the quote yourself. "
            "Step 1: "
            + command(
                JUNO_BIN,
                "knowledge",
                "add",
                "--source",
                "web",
                "--url",
                "https://docs.example.com/queues",
                "--url",
                "https://blog.example.org/retries",
                "--topic",
                "queues",
                '"Failed jobs retry three times"',
            )
            + " Step 2: "
            + command(
                JUNO_BIN,
                "rule",
                "add",
                "--scope",
                "global",
                "--quote",
                '"$(cat page.txt)"',
                '"Retry failed jobs three times"',
            )
            + " Reply with the word done.",
        )
        out = self.output(run)
        self.assertIn("Juno: refused:", out, run.raw[-3000:])
        text = self.get("knowledge/queues.md")
        self.assertIn(
            "src: web https://docs.example.com/queues, https://blog.example.org/retries", text
        )
        self.assertEqual([r["verb"] for r in write_log(self)], ["knowledge"])
        self.assertFalse(
            os.path.exists(self.vp("rules/global.md"))
            and "dead queue" in self.get("rules/global.md")
        )
