import json
import os

from juno.adapters import learnio
from juno.core import topics
from tests.support.cardcase import CardCase

DIGEST = "Commands: git tag v1.2.0 ; git push origin v1.2.0 ; gh run watch\n"
STEPS = ["Run git tag v1.2.0", "Run git push origin v1.2.0", "gh run watch"]


def item(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "slug": "release-tag",
        "title": "Tag and ship a release",
        "steps": STEPS,
        "triggers": ["git tag"],
        "repos": ["billing-api", "elsewhere"],
    }
    base.update(over)
    return base


class Extraction(CardCase):
    def learn(self, *items: object) -> dict[str, str]:
        self.dirty()
        raw = json.dumps({"procedures": list(items)})
        return learnio.apply(self.cfg(), self.seg(), raw, DIGEST, [])[1]

    def test_written_with_front_matter_and_steps(self) -> None:
        sections = self.learn(item())
        self.assertEqual(sections["procedures"], "ok")
        text = self.read("procedures/release-tag.md")
        self.assertIn("title: Tag and ship a release", text)
        self.assertIn("triggers: [git tag]", text)
        self.assertIn("repos: [billing-api]", text)
        self.assertIn("2. Run git push origin v1.2.0", text)

    def test_only_the_first_item_is_considered(self) -> None:
        self.learn(item(), item(slug="other-one"))
        self.assertFalse(os.path.exists(self.vp("procedures/other-one.md")))

    def test_unanchored_step_is_dropped(self) -> None:
        steps = [*STEPS, "Restart the invented service"]
        self.assertEqual(self.learn(item(steps=steps))["procedures"], "dropped:unanchored")
        self.assertFalse(os.path.exists(self.vp("procedures/release-tag.md")))

    def test_step_count_bounds(self) -> None:
        for steps in (STEPS[:1], [STEPS[0]] * 13):
            self.assertEqual(self.learn(item(steps=steps))["procedures"], "dropped:invalid")

    def test_bad_slug_trigger_and_secret(self) -> None:
        self.assertEqual(self.learn(item(slug="Bad Slug"))["procedures"], "dropped:invalid")
        self.assertEqual(self.learn(item(triggers=["a"]))["procedures"], "dropped:trigger")
        many = ["alpha1", "beta22", "gamma3", "delta4", "eps55", "zeta66"]
        self.assertEqual(self.learn(item(triggers=many))["procedures"], "dropped:trigger")
        secret = "Run git tag v1.2.0 password=abc123def456ghi789"
        self.assertEqual(
            self.learn(item(steps=[secret, STEPS[1]]))["procedures"], "dropped:private"
        )

    def test_same_slug_is_rewritten(self) -> None:
        self.learn(item())
        self.learn(item(title="Tag and ship it again"))
        self.assertIn("Tag and ship it again", self.read("procedures/release-tag.md"))


class Trigger(CardCase):
    def bash(self, command: str) -> str:
        return self.context(
            self.hook(
                "pre_tool_use",
                self.payload(
                    "PreToolUse",
                    tool_name="Bash",
                    tool_input={"command": command},
                    tool_use_id="t1",
                ),
            )
        )

    def test_bash_command_injects_once_per_segment(self) -> None:
        self.write(
            "procedures/release-tag.md",
            topics.procedure_text("Tag and ship", "2026-11-07", "abcd1234", STEPS, ["git tag"], []),
        )
        text = self.bash("git tag v2")
        self.assertIn("[Procedure · procedures/release-tag.md] Tag and ship", text)
        self.assertIn("2. Run git push origin v1.2.0", text)
        self.assertEqual(self.bash("git tag v3"), "")

    def test_block_is_capped(self) -> None:
        steps = ["Run step number %02d " % n + "x" * 150 for n in range(12)]
        self.write(
            "procedures/long.md",
            topics.procedure_text("Long one", "2026-11-07", "abcd1234", steps, ["longproc"], []),
        )
        block = topics.procedure_block(
            __import__("juno.adapters.vaultview", fromlist=["x"]).DiskVault(self.cfg()),
            "procedures/long.md",
        )
        self.assertLessEqual(len(block), topics.PROCEDURE_BLOCK_CHARS)
        self.assertIn("1. Run step number 00", block)

    def test_archived_procedure_is_silent(self) -> None:
        text = topics.procedure_text("Old", "2026-11-07", "abcd1234", STEPS, ["git tag"], [])
        self.write("procedures/old.md", text.replace("title: Old", "title: Old\nstatus: archived"))
        self.assertEqual(self.bash("git tag v2"), "")
