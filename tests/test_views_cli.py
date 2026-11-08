import json
import os
import unittest

from tests.support.write import WriteCase


def card(day: str, hour: str, seg: str, learned: str = "none") -> str:
    return f"## {day} {hour} · - · work · {seg}\nRepos: app\nDid something.\nLearned: {learned}\n\n"


class Ideas(WriteCase):
    def setUp(self) -> None:
        super().setUp()
        self.put(
            "ideas/alpha.md",
            "---\nstatus: seed\nupdated: 2026-10-01\ntags: [x, y]\nsource: chat\n---\n\n## Idea\nFirst idea title\n",
        )
        self.put(
            "ideas/beta.md",
            "---\nstatus: in-progress\nupdated: 2026-10-02\ntags: [y]\n---\n\n## Idea\nSecond\n",
        )
        self.put(
            "ideas/gone.md", "---\nstatus: seed\nvisibility: private\n---\n\n## Idea\nHidden\n"
        )
        self.put("ideas/plain.md", "no frontmatter at all\n")

    def test_grouped_sorted_and_hidden_skipped(self) -> None:
        out = self.juno("ideas")[1]
        self.assertLess(out.index("in-progress:"), out.index("seed:"))
        self.assertIn("alpha · 2026-10-01 · x, y · First idea title · chat", out)
        self.assertNotIn("Hidden", out)
        self.assertNotIn("plain", out)

    def test_filters_and_json(self) -> None:
        self.assertNotIn("alpha", self.juno("ideas", "--status", "in-progress")[1])
        rows = json.loads(self.juno("ideas", "--tag", "x", "--json")[1])["ideas"]
        self.assertEqual([r["id"] for r in rows], ["alpha"])

    def test_read_only_for_subagents_and_no_dir(self) -> None:
        self.assertEqual(self.juno("ideas", env={"CLAUDE_CODE_AGENT_ID": "a"})[0], 0)
        os.rename(self.vp("ideas"), self.vp("ideas-gone"))
        self.assertEqual(self.juno("ideas")[1], "no ideas\n")


class Tasks(WriteCase):
    def test_without_jira_prints_a_note(self) -> None:
        self.assertEqual(self.juno("tasks"), (0, "jira not configured\n"))

    def test_lists_filters(self) -> None:
        self.set_config('[jira]\nprefixes = ["ABC"]\n')
        self.put("tasks/ABC-1.md", "---\ntitle: One\nstatus: open\nrepos: [app, web]\n---\n")
        self.put("tasks/ABC-2.md", "---\ntitle: Two\nstatus: done\nrepos: [api]\n---\n")
        out = self.juno("tasks")[1]
        self.assertIn("ABC-1 · open · One · app, web", out)
        self.assertEqual(self.juno("tasks", "--status", "done")[1].count("ABC-"), 1)
        rows = json.loads(self.juno("tasks", "--repo", "api", "--json")[1])["tasks"]
        self.assertEqual([r["key"] for r in rows], ["ABC-2"])


class Digest(WriteCase):
    def test_rebuilds_learned_from_cards(self) -> None:
        self.put(
            "handoffs/app.md",
            "# Handoffs\n\n"
            + card("2026-10-01", "10:00", "aaaa1111", "Retries need jitter.")
            + card("2026-10-02", "10:00", "bbbb2222")
            + card("2026-10-03", "10:00", "cccc3333", "Cache keys include the tenant."),
        )
        self.put(
            "handoffs/secret.md",
            "---\nvisibility: private\n---\n" + card("2026-10-04", "10:00", "dddd4444", "Hidden."),
        )
        code, out = self.juno("digest")
        self.assertEqual(code, 0, out)
        text = self.get("_generated/learned.md")
        self.assertIn("- 2026-10-03 · Cache keys include the tenant. · handoffs/app.md", text)
        self.assertLess(text.index("10-03"), text.index("10-01"))
        self.assertNotIn("Hidden", text)
        self.assertNotIn("10-02", text)
        self.assertTrue(text.endswith("\n"))

    def test_refused_without_a_session(self) -> None:
        code, out = self.juno("digest", env={"CLAUDE_CODE_SESSION_ID": ""})
        self.assertEqual((code, out.startswith("Juno: refused: ")), (2, True))


class Compact(WriteCase):
    def fill(self, count: int, name: str = "app") -> str:
        text = "# Handoffs · app\n\n"
        for n in range(count):
            month = "2026-09" if n < 12 else "2026-10"
            text += card(f"{month}-{n % 28 + 1:02d}", "09:00", f"{n:08x}", f"Learned {n}.")
        return self.put(f"handoffs/{name}.md", text)

    def test_moves_the_oldest_cards_to_the_monthly_archive(self) -> None:
        self.fill(25)
        code, out = self.juno("compact")
        self.assertEqual(code, 0, out)
        kept = self.get("handoffs/app.md")
        self.assertEqual(kept.count("\n## 20"), 20)
        self.assertTrue(kept.startswith("# Handoffs · app"))
        archive = self.get("archive/handoffs/2026-09.md")
        self.assertTrue(archive.startswith("---\nvisibility: private\n---"))
        self.assertIn("## From handoffs/app.md", archive)
        self.assertEqual(archive.count("\n## 20"), 5)
        self.assertEqual(
            oct(os.stat(self.vp("archive/handoffs/2026-09.md")).st_mode & 0o777), "0o600"
        )

    def test_rerun_is_a_no_op_and_small_files_stay(self) -> None:
        self.fill(25)
        self.juno("compact")
        before = self.get("handoffs/app.md"), self.get("archive/handoffs/2026-09.md")
        code, out = self.juno("compact")
        self.assertEqual((code, "nothing to move" in out), (0, True))
        self.assertEqual(
            (self.get("handoffs/app.md"), self.get("archive/handoffs/2026-09.md")), before
        )
        self.fill(20, "small")
        self.juno("compact")
        self.assertEqual(self.get("handoffs/small.md").count("\n## 20"), 20)

    def test_crash_after_archive_does_not_duplicate(self) -> None:
        path = self.fill(22)
        original = self.get("handoffs/app.md")
        self.juno("compact")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(original)
        self.juno("compact")
        self.assertEqual(self.get("archive/handoffs/2026-09.md").count("\n## 2026-09"), 2)
        self.assertEqual(self.get("handoffs/app.md").count("\n## 20"), 20)

    def test_cards_inside_a_fence_are_not_split(self) -> None:
        text = "# Handoffs\n\n```\n" + card("2026-01-01", "09:00", "11111111") + "```\n\n"
        text += "".join(card("2026-02-01", "09:00", f"{n:08x}") for n in range(21))
        self.put("handoffs/app.md", text)
        self.assertEqual(self.juno("compact")[0], 0)
        self.assertIn("```", self.get("handoffs/app.md"))

    def test_json_and_refusal(self) -> None:
        self.fill(21)
        data = json.loads(self.juno("compact", "--json")[1])
        self.assertEqual(data["moved"], {"handoffs/app.md": 1})
        self.assertEqual(self.juno("compact", env={"CLAUDE_CODE_AGENT_ID": "a"})[0], 2)


if __name__ == "__main__":
    unittest.main()
