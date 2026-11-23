import contextlib
import io
import json
import os
import stat
from typing import Any

from tests.support import SandboxCase
from tests.support.hooks import SID, HookCase

ALPHA = (
    "# Alpha\n\nThe zebra migration moved the billing tables first. The rollback finishes within "
    "ten minutes when the flag is flipped.\n"
)
BETA = "# Beta\n\nCompletely unrelated gardening advice about tomatoes and compost.\n"


class SelftestCase(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.put("notes/alpha.md", ALPHA)
        self.put("notes/beta.md", BETA)

    def put(self, rel: str, text: str) -> None:
        path = os.path.join(self.sb.vault, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def st(self, *argv: str, env: dict[str, str] | None = None) -> tuple[int, str]:
        from juno import cli

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["selftest", *argv], {**self.env, **(env or {})})
        return code, buffer.getvalue()

    def add(self, *argv: str) -> dict[str, Any]:
        code, out = self.st("add", "--json", *argv)
        self.assertEqual(code, 0, out)
        row: dict[str, Any] = json.loads(out)
        return row

    def run_json(self, *argv: str) -> dict[str, Any]:
        code, out = self.st("run", "--json", *argv)
        self.assertEqual(code, 0, out)
        report: dict[str, Any] = json.loads(out)
        return report

    def state_text(self) -> str:
        path = os.path.join(self.sb.state, "selftest.json")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as handle:
            return handle.read()


class Roundtrip(SelftestCase):
    def test_add_list_remove(self) -> None:
        first = self.add(
            "zebra migration", "--expect", "notes/alpha.md", "--sentence", "ten minutes"
        )
        second = self.add("any weather", "--control")
        self.assertEqual((first["id"], second["id"]), ("q1", "q2"))
        self.assertEqual(first["expect"], "notes/alpha.md")
        self.assertFalse(first["control"])
        self.assertTrue(second["control"])
        self.assertIsNone(second["expect"])
        rows = json.loads(self.st("list", "--json")[1])["questions"]
        self.assertEqual([r["id"] for r in rows], ["q1", "q2"])
        self.assertIn("notes/alpha.md", self.st("list")[1])
        self.assertEqual(self.st("remove", "q1")[0], 0)
        rows = json.loads(self.st("list", "--json")[1])["questions"]
        self.assertEqual([r["id"] for r in rows], ["q2"])
        self.assertEqual(self.add("x y", "--expect", "notes/beta.md")["id"], "q3")
        self.assertEqual(self.st("remove", "q9")[0], 2)

    def test_state_file_is_private_and_outside_the_vault(self) -> None:
        self.add("zebra", "--expect", "notes/alpha.md")
        path = os.path.join(self.sb.state, "selftest.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertFalse(os.path.exists(os.path.join(self.sb.vault, "selftest.json")))

    def test_empty_list(self) -> None:
        self.assertEqual(self.st("list"), (0, "no questions\n"))
        self.assertEqual(json.loads(self.st("list", "--json")[1]), {"questions": []})


class PathValidation(SelftestCase):
    def refused(self, *argv: str) -> str:
        code, out = self.st("add", *argv)
        self.assertEqual(code, 2, out)
        self.assertEqual(self.state_text(), "")
        return out

    def test_bad_paths_are_refused(self) -> None:
        self.refused("q", "--expect", "notes/missing.md")
        self.refused("q", "--expect", "../outside.md")
        self.refused("q", "--expect", "notes/../notes/alpha.md")
        self.refused("q", "--expect", "/etc/passwd")
        self.refused("q", "--expect", os.path.join(os.path.dirname(self.sb.vault), "x.md"))
        self.refused("q", "--expect", "notes")
        self.refused("q", "--expect", "notes/alpha.txt")
        self.refused("q", "--expect", "")

    def test_secrets_and_personal_data_are_refused(self) -> None:
        self.refused("the PRIVATE_KEY=abcdefghij12345678 setting", "--control")
        self.refused("mail me at someone@example.com", "--expect", "notes/alpha.md")
        self.refused("zebra", "--expect", "notes/alpha.md", "--sentence", "ask someone@example.com")
        self.refused(
            "zebra", "--expect", "notes/alpha.md", "--sentence", "PRIVATE_KEY=abcdefghij12345678"
        )

    def test_symlinks_are_refused(self) -> None:
        os.symlink(os.path.join(self.sb.vault, "notes", "alpha.md"), self.vp("notes/link.md"))
        self.assertIn("symlink", self.refused("q", "--expect", "notes/link.md"))
        os.symlink(os.path.join(self.sb.vault, "notes"), self.vp("linked"))
        self.assertIn("symlink", self.refused("q", "--expect", "linked/alpha.md"))

    def test_absolute_path_inside_the_vault_is_stored_relative(self) -> None:
        row = self.add("q", "--expect", self.vp("notes/alpha.md"))
        self.assertEqual(row["expect"], "notes/alpha.md")

    def test_expect_or_control_is_needed_and_not_both(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.st("add", "q")
            with self.assertRaises(SystemExit):
                self.st("add", "q", "--control", "--expect", "notes/alpha.md")
        self.refused("q", "--control", "--sentence", "x")
        self.refused("   ", "--control")


class Run(SelftestCase):
    def test_found_missed_and_sentence_cut_by_budget(self) -> None:
        self.add(
            "zebra migration billing", "--expect", "notes/alpha.md", "--sentence", "ten minutes"
        )
        self.add("zebra migration", "--expect", "notes/beta.md")
        self.add("rollback flag", "--expect", "notes/alpha.md", "--sentence", "no such phrase")
        report = self.run_json()
        found, miss, absent = report["results"]
        self.assertEqual((found["rank"], found["hit1"], found["hitk"]), (1, True, True))
        self.assertEqual(found["sentence"], "found")
        self.assertEqual((miss["rank"], miss["hit1"], miss["hitk"]), (None, False, False))
        self.assertEqual(absent["sentence"], "absent")
        summary = report["summary"]
        self.assertEqual(
            (summary["questions"], summary["hit_at_1"], summary["hit_at_k"]), (3, 2, 2)
        )
        self.assertEqual(summary["missed"], 1)
        self.assertEqual(report["limit"], 5)
        cut = self.run_json("--budget", "40")
        self.assertEqual(cut["results"][0]["sentence"], "cut_by_budget")
        self.assertEqual(cut["summary"]["sentence_cut_by_budget"], 1)
        self.assertEqual(cut["summary"]["sentence_absent"], 1)
        self.assertEqual(cut["results"][0]["rank"], 1)
        roomy = self.run_json("--budget", "5000")
        self.assertEqual(roomy["results"][0]["sentence"], "found")

    def test_budget_is_spent_in_rank_order_across_snippets(self) -> None:
        from juno.core import selftest

        hits = [
            {"relpath": "a.md", "snippet": "x" * 30},
            {"relpath": "b.md", "snippet": "the needle is here"},
        ]
        row = {
            "id": "q1",
            "question": "q",
            "expect": "b.md",
            "control": False,
            "sentence": "needle",
        }
        self.assertEqual(selftest.score(row, hits, 31)["sentence"], "cut_by_budget")
        self.assertEqual(selftest.score(row, hits, 41)["sentence"], "found")
        self.assertEqual(selftest.score(row, hits, None)["sentence"], "found")

    def test_controls(self) -> None:
        self.add("qqqnothingmatches", "--control")
        self.add("gardening tomatoes", "--control")
        results = self.run_json()["results"]
        self.assertEqual([r["passed"] for r in results], [True, False])
        summary = self.run_json()["summary"]
        self.assertEqual((summary["controls"], summary["controls_passed"]), (2, 1))
        self.assertEqual(summary["questions"], 0)
        self.assertIn("controls: passed 1/2", self.st("run")[1])

    def test_limit_is_the_search_limit(self) -> None:
        self.add("zebra gardening", "--expect", "notes/beta.md")
        self.assertEqual(self.run_json("--limit", "1")["limit"], 1)
        self.assertEqual(self.st("run", "--limit", "0")[0], 2)
        self.assertEqual(self.st("run", "--budget", "-1")[0], 2)

    def test_results_agree_with_juno_search(self) -> None:
        self.add("zebra migration", "--expect", "notes/alpha.md")
        seen = json.loads(self.sb.cli("search", "zebra migration", "--json")[1])["results"]
        res = self.run_json()["results"][0]
        self.assertEqual(res["returned"], len(seen))
        self.assertEqual(res["rank"], [r["relpath"] for r in seen].index("notes/alpha.md") + 1)

    def test_stem_fallback_answers_like_juno_search(self) -> None:
        self.put("notes/kitap.md", "# Kitap\n\nokunacak kitap listesi\n")
        self.add("kitabı", "--expect", "notes/kitap.md")
        seen = json.loads(self.sb.cli("search", "kitabı", "--json")[1])
        self.assertIn("stems", seen)
        res = self.run_json()["results"][0]
        self.assertEqual(
            res["rank"], [r["relpath"] for r in seen["results"]].index("notes/kitap.md") + 1
        )
        self.assertTrue(res["hit1"])

    def test_text_output_and_json_shape(self) -> None:
        self.add("zebra migration", "--expect", "notes/alpha.md", "--sentence", "ten minutes")
        out = self.st("run")[1]
        self.assertIn("hit@1 1/1", out)
        self.assertIn("sentences: found 1", out)
        report = self.run_json()
        self.assertEqual(set(report), {"limit", "budget", "results", "summary"})
        self.assertEqual(
            set(report["results"][0]),
            {"id", "question", "expect", "control", "returned", "rank", "hit1", "hitk",
             "sentence", "passed"},
        )  # fmt: skip
        self.assertIn("wilson_lower_hit_at_k", report["summary"])

    def test_no_questions(self) -> None:
        self.assertEqual(self.st("run"), (0, "no questions\n"))
        self.assertEqual(self.run_json()["summary"]["questions"], 0)


class Refusals(SelftestCase):
    def test_subagent_refuses_add_and_remove_but_not_list_and_run(self) -> None:
        self.add("zebra", "--expect", "notes/alpha.md")
        before = self.state_text()
        env = {"CLAUDE_CODE_AGENT_ID": "a1"}
        code, out = self.st("add", "q", "--control", env=env)
        self.assertEqual(code, 2)
        self.assertIn("agent context", out)
        self.assertEqual(self.st("remove", "q1", env=env)[0], 2)
        self.assertEqual(self.state_text(), before)
        self.assertEqual(self.st("list", env=env)[0], 0)
        self.assertEqual(self.st("run", env=env)[0], 0)

    def test_nosave_segment_refuses(self) -> None:
        self.add("zebra", "--expect", "notes/alpha.md")
        self.hook("user_prompt_submit", self.payload("user_prompt_submit", prompt="hi [nosave]"))
        before = self.state_text()
        env = {"CLAUDE_CODE_SESSION_ID": SID}
        code, out = self.st("add", "q", "--control", env=env)
        self.assertEqual(code, 2)
        self.assertIn("[nosave]", out)
        self.assertEqual(self.st("remove", "q1", env=env)[0], 2)
        self.assertEqual(self.state_text(), before)
        self.assertEqual(self.st("list", env=env)[0], 0)
        self.assertEqual(self.st("run", env=env)[0], 0)

    def test_private_segment_refuses(self) -> None:
        self.hook("user_prompt_submit", self.payload("user_prompt_submit", prompt="hello there"))
        ledger = os.path.join(self.sb.state, "ledgers", SID + ".jsonl")
        with open(ledger, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"ev": "private", "t": 5.0}) + "\n")
        env = {"CLAUDE_CODE_SESSION_ID": SID}
        self.assertEqual(self.st("add", "q", "--control", env=env)[0], 2)
        self.assertEqual(self.state_text(), "")

    def test_normal_session_and_plain_shell_are_allowed(self) -> None:
        self.hook("user_prompt_submit", self.payload("user_prompt_submit", prompt="hello there"))
        env = {"CLAUDE_CODE_SESSION_ID": SID}
        self.assertEqual(self.st("add", "q", "--control", env=env)[0], 0)
        self.assertEqual(self.st("add", "r", "--control")[0], 0)


class Cli(SandboxCase):
    def test_verb_is_registered_with_a_pinned_prog(self) -> None:
        from juno import cli

        self.assertEqual(cli.build_parser("selftest").prog, "juno selftest")
