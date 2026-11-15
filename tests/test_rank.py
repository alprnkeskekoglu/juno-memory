import json
import os
import unittest

from juno.adapters import shadowio
from juno.adapters.vaultview import DiskVault
from juno.core import rank
from juno.handlers import delivery
from tests.support.cardcase import KEY, CardCase
from tests.test_arms import put_arm


class Scoring(unittest.TestCase):
    def test_query_text_joins_prompt_and_tool_input_with_paths_made_relative(self) -> None:
        text = rank.query_text(
            "fix it", {"file_path": "/w/demo/migrations/001_init.sql", "command": "ls"}, "/w/demo"
        )
        self.assertEqual(text, "fix it\nmigrations/001_init.sql\nls")
        self.assertEqual(rank.query_text("", {}, "/w"), "")

    def test_bm25_prefers_the_rarer_matching_term(self) -> None:
        items = ["- the cache is cold", "- the migrations run twice", "- the build is slow"]
        scores = rank.bm25(items, "the migrations")
        self.assertEqual(scores.index(max(scores)), 1)

    def test_no_match_means_no_ranking(self) -> None:
        self.assertIsNone(rank.ranked_indices(["- one thing", "- another"], 100, "zzz"))
        self.assertIsNone(rank.ranked_indices(["- one thing", "- another"], 100, ""))


class Delivery(CardCase):
    QUERY = "migrations/001_init.sql"

    def setUp(self) -> None:
        super().setUp()
        self.set_config("[caps]\npack_chars = 3000\n")
        lines = [f"- note {i} about area{i} {'x' * 130}" for i in range(1, 41)]
        lines[0] = f"- note 1 about migrations/ ordering {'x' * 120}"
        self.write(f"tasks/{KEY}.md", f"# {KEY}\n\n## Notes\n" + "\n".join(lines) + "\n")

    def deliver(self, query: str = QUERY) -> str:
        cfg = self.cfg()
        self.delivered = getattr(self, "delivered", 0) + 1
        seg = self.seg(f"rank-sid-{self.delivered}")
        return delivery.deliver_packs(cfg, DiskVault(cfg), seg, [("ticket", KEY)], 6000, query)

    def trim_log(self) -> list[dict[str, object]]:
        path = os.path.join(self.sb.state, shadowio.TRIM_FILE)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def test_default_keeps_the_newest_and_logs_the_ranked_choice_in_shadow(self) -> None:
        text = self.deliver()
        self.assertNotIn("note 1 about", text)
        self.assertIn("note 40 ", text)
        (entry,) = self.trim_log()
        self.assertFalse(entry["on"])
        sections = entry["sections"]
        assert isinstance(sections, list)
        self.assertIn(0, sections[0]["ranked"])
        self.assertNotIn(0, sections[0]["positional"])

    def test_no_query_logs_nothing(self) -> None:
        self.assertNotIn("note 1 about", self.deliver(""))
        self.assertEqual(self.trim_log(), [])

    def test_config_key_turns_the_ranked_trim_on(self) -> None:
        self.set_config("[packs]\nranked_trim = true\n")
        text = self.deliver()
        self.assertIn("note 1 about migrations/", text)
        self.assertTrue(self.trim_log()[0]["on"])

    def test_a_running_ranked_arm_decides_over_the_config_key(self) -> None:
        self.set_config("[packs]\nranked_trim = true\n")
        put_arm(self.sb.state, "ranked", fraction=0.0)
        self.assertNotIn("note 1 about", self.deliver())

    def test_a_running_ranked_arm_on_side_turns_it_on_without_the_config_key(self) -> None:
        put_arm(self.sb.state, "ranked", fraction=1.0)
        self.assertIn("note 1 about migrations/", self.deliver())
