import os
from unittest import mock

from juno.adapters import cardrun
from juno.core import routing
from tests.support import REPO
from tests.support.cardcase import KEY, CardCase

WRITE = bool(os.environ.get("JUNO_WRITE_GOLDEN"))
FIXTURES = os.path.join(REPO, "tests", "fixtures")
DIGEST = (
    "Learning moments:\n- user corrected the rounding check in src/money.py\n"
    "Commands: pytest tests/test_money.py passed; fix-rounding is the open backlog item\n"
)


def rule(text: str) -> str:
    return f"- 2026-10-01 · {text} · src: unquoted\n"


class CardPromptGolden(CardCase):
    def seed_vault(self) -> None:
        self.write(
            "rules/global.md",
            "# Rules: global\n\n" + rule("Reply in Turkish") + rule("Keep diffs small"),
        )
        self.write(
            "rules/repos/billing-api.md", "# Rules: repo:billing-api\n\n" + rule("Run make lint")
        )
        self.write(
            "rules/topics/testing.md",
            "# Rules: topic:testing\n\ntriggers: pytest, make test\n\n"
            + rule("Run one test file first"),
        )
        self.write(
            "projects/billing-api.md",
            "---\nrepo: billing-api\nupdated: 2026-10-01\n---\n## Purpose\nBilling.\n\n"
            "## Gotchas\n- Money rounding lives in src/money.py\n",
        )
        self.write("knowledge/billing.md", "---\ntitle: Billing\n---\n## Notes\n- x\n")
        self.write(
            "pitfalls.md", "# Pitfalls\n\n- Wrong branch checked out before commit · when: git\n"
        )
        self.write(
            "threads.md",
            "# Threads\n\n- [ABC-111111] Ask finance about rounding · since 2026-10-02\n"
            "- [other] unrelated · since 2026-01-01\n",
        )
        self.write(
            "backlog/fix-rounding.md",
            "---\ntitle: Fix rounding\nstatus: doing\npriority: p2\nadded: 2026-11-01\n"
            "updated: 2026-11-01\ntickets: [ABC-111111]\nrepos: [billing-api]\n---\n## Task\nx\n",
        )

    def build_prompt(self, seed: bool) -> str:
        self.dirty()
        if seed:
            self.seed_vault()
        cfg = self.cfg()
        seg = self.seg()
        targets = routing.card_targets(seg)
        with mock.patch("os.urandom", return_value=bytes(6)):
            return cardrun.prompt_for(cfg, seg, targets, DIGEST, "2026-11-07 10:00", False, True)

    def check(self, text: str, name: str) -> None:
        self.assertIn(KEY, text)
        path = os.path.join(FIXTURES, name)
        if WRITE:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(text, handle.read())

    def test_seeded_vault_prompt_is_byte_identical(self) -> None:
        self.check(self.build_prompt(True), "card_prompt_seeded.txt")

    def test_empty_vault_prompt_is_byte_identical(self) -> None:
        self.check(self.build_prompt(False), "card_prompt_empty.txt")
