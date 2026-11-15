import unittest

from juno.adapters import stalefix as adapter
from juno.cmd import stalefix as cmd
from juno.core import stale
from juno.core import stalefix as core
from tests.support.stalefix import StaleCase, day
from tests.test_stale import Base, days_ago

DECISION = "- keep the exporter in `src/old.py` (user abcd1234, 2026-01-01)"
GOTCHA = "- The legacy exporter lives in `src/old.py`"


def profile(*sections: str, verified: int = 120) -> str:
    return f"---\nrepo: app\nverified: {day(verified)}\n---\n# app\n\n" + "\n".join(sections) + "\n"


DECISIONS = f"## Decisions\n\n{DECISION}\n"
GOTCHAS = f"## Gotchas\n\n{GOTCHA}\n"


class Helper(unittest.TestCase):
    def test_span_follows_the_section_boundaries_of_vaultfmt(self) -> None:
        lines = ["# a", "## Gotchas", "- g", "## Decisions", "", "- d", "# top", "- t"]
        self.assertEqual(stale.decisions_span(lines), (3, 6))
        self.assertTrue(stale.in_decisions(lines, 5))
        self.assertFalse(stale.in_decisions(lines, 2))
        self.assertFalse(stale.in_decisions(lines, 7))
        self.assertEqual(
            stale.without_decisions("\n".join(lines)), "\n".join(lines[:3] + lines[6:])
        )
        self.assertIsNone(stale.decisions_span(["## Gotchas", "- g"]))


class Scan(Base):
    def test_a_path_cited_only_in_decisions_does_not_mark_a_profile_stale(self) -> None:
        self.commit("app/x.py", days_ago(3))
        head = f"---\nrepo: hb\nverified: {days_ago(10)}\n---\n"
        self.write("projects/hb.md", head + "## Decisions\n\n- use `app/x.py` always\n")
        self.write("projects/hb2.md", head + "## Gotchas\n- see `app/x.py`\n")
        report = self.stale()
        quiet, loud = self.entry(report, "projects/hb.md"), self.entry(report, "projects/hb2.md")
        self.assertEqual((quiet["stale"], quiet["drift"]), (False, []))
        self.assertTrue(loud["stale"])

    def test_knowledge_files_keep_their_decisions_section(self) -> None:
        self.commit("app/x.py", days_ago(3))
        text = f"---\nrepos: [hb]\nupdated: {days_ago(10)}\n---\n## Decisions\n- use `app/x.py`\n"
        self.write("knowledge/k.md", text)
        self.assertTrue(self.entry(self.stale(), "knowledge/k.md")["stale"])


class Fixer(StaleCase):
    def test_a_profile_with_only_decision_citations_yields_no_candidate(self) -> None:
        self.put("projects/app.md", profile(DECISIONS))
        out, calls = self.run_job([])
        reasons = {s["file"]: s["reason"] for s in out.row["skipped"]}
        self.assertEqual(reasons["projects/app.md"], "no code anchor")
        self.assertFalse([p for p in calls.prompts if "projects/app.md" in p])

    def test_the_same_citation_in_gotchas_is_still_a_candidate(self) -> None:
        self.put("projects/app.md", profile(GOTCHAS, DECISIONS))
        _, calls = self.run_job([])
        prompt = next(p for p in calls.prompts if "projects/app.md" in p)
        self.assertIn(GOTCHA, prompt)
        self.assertNotIn(DECISION, prompt)

    def test_candidate_lines_skip_a_decisions_section_only_when_asked(self) -> None:
        lines = ["## Decisions", "", "- a `x/y.py`", "## Gotchas", "- b `x/y.py`"]
        both, _ = core.candidate_lines(lines, ["x/y.py"], 5)
        only, _ = core.candidate_lines(lines, ["x/y.py"], 5, skip_decisions=True)
        self.assertEqual([c.line for c in both], [3, 5])
        self.assertEqual([c.line for c in only], [5])

    def test_apply_refuses_a_line_inside_decisions(self) -> None:
        text = profile(DECISIONS)
        self.put("projects/app.md", text)
        lines = text.splitlines()
        number = lines.index(DECISION) + 1
        cand = core.Candidate(number, DECISION, ("src/old.py",))
        scan = adapter.FileScan(
            "projects/app.md", day(120), 120, ["app"], {}, {}, [cand], False, 0, lines
        )
        verdict = core.Verdict(number, "GONE", "", (), "")
        row = {"skipped": [], "writes": [], "rejected": [], "fixed": 0, "archived": 0}
        cmd.commit_changes(
            self.cfg(),
            None,
            scan,
            core.FilePlan(writes=[verdict]),
            [verdict],
            "r",
            day(0),
            row,  # type: ignore[arg-type]
        )
        self.assertEqual(row["writes"], [])
        self.assertIn("Decisions", row["rejected"][0]["reason"])
        self.assertEqual(self.get("projects/app.md"), text)


if __name__ == "__main__":
    unittest.main()
