import json
import os
import unittest

from juno.adapters import queues
from tests.support.write import WriteCase

QUOTE = "always run the formatter before you commit anything"
RULE = "Run the formatter before every commit"


class Add(WriteCase):
    def setUp(self) -> None:
        super().setUp()
        self.prompt(QUOTE + ", please remember that", "pid00002")

    def add(self, *extra: str, rule: str = RULE, quote: str = QUOTE) -> tuple[int, str]:
        return self.juno("rule", "add", "--quote", quote, *extra, rule)

    def test_typed_quote_writes_the_line_and_the_event(self) -> None:
        code, out = self.add("--scope", "global")
        self.assertEqual(code, 0, out)
        text = self.get("rules/global.md")
        self.assertRegex(
            text,
            r'- \d{4}-\d\d-\d\d · Run the formatter before every commit · src: user pid00002 · "always',
        )
        added = [e for e in self.events() if e["ev"] == "rule_added"]
        self.assertEqual(added[0]["scope"], "global")
        with open(os.path.join(self.sb.state, "status.json"), encoding="utf-8") as handle:
            status = json.load(handle)
        self.assertEqual(status["last_rule"]["scope"], "global")
        self.assertEqual(status["rules_added_seq"], 1)

    def test_chosen_answer_counts_as_a_source(self) -> None:
        self.answer("Use the shared lint config in every repository we touch")
        code, out = self.juno(
            "rule",
            "add",
            "--scope",
            "repo:app",
            "--quote",
            "Use the shared lint config in every repository",
            "Use the shared lint config",
        )
        self.assertEqual(code, 0, out)
        self.assertIn("rules/repos/app.md", out)

    def test_fabricated_quote_is_refused(self) -> None:
        code, out = self.add(
            "--scope", "global", quote="I never said this sentence at all, formatter before commit"
        )
        self.assertEqual(code, 2)
        self.assertIn("Juno: refused: quote is not a verbatim part", out)
        self.assertEqual(self.get("rules/global.md").count("\n- "), 0)

    def test_quote_needs_a_word_in_common_with_the_rule(self) -> None:
        code, out = self.add("--scope", "global", rule="Prefer tabs", quote=QUOTE)
        self.assertEqual(code, 2)
        self.assertIn("meaningful word", out)

    def test_short_quote_refused(self) -> None:
        code, out = self.add("--scope", "global", quote="formatter")
        self.assertEqual((code, "shorter than 20" in out), (2, True))

    def test_turkish_folding_with_the_tr_preset(self) -> None:
        self.set_config('[language]\npreset = "tr"\n')
        self.prompt("HER ZAMAN ŞİFRELERİ güvenli saklayın lütfen bundan sonra", "pid00003")
        code, out = self.juno(
            "rule",
            "add",
            "--scope",
            "global",
            "--quote",
            "her zaman sifreleri guvenli saklayin",
            "Sifreleri guvenli sakla",
        )
        self.assertEqual(code, 0, out)

    def test_refusals_by_context(self) -> None:
        cases = {
            "agent": {"CLAUDE_CODE_AGENT_ID": "a1"},
            "headless": {"JUNO_TEST_INTERACTIVE": "", "CLAUDE_CODE_SESSION_ATTENDED": "0"},
            "no session": {"CLAUDE_CODE_SESSION_ID": ""},
            "no ledger": {"CLAUDE_CODE_SESSION_ID": "ffffffff-0000-4000-8000-000000000000"},
        }
        for name, extra in cases.items():
            with self.subTest(name):
                code, out = self.juno(
                    "rule", "add", "--scope", "global", "--quote", QUOTE, RULE, env=extra
                )
                self.assertEqual(code, 2)
                self.assertTrue(out.startswith("Juno: refused: "), out)
        self.assertEqual(self.get("rules/global.md").count("\n- "), 0)

    def test_nosave_blocks_the_write(self) -> None:
        self.prompt("[nosave] no memory in this one", "pid00005", nosave=True)
        code, out = self.add("--scope", "global")
        self.assertEqual((code, out), (2, "Juno: refused: [nosave] is set in this segment\n"))

    def test_caps_and_shape(self) -> None:
        self.assertEqual(self.add("--scope", "global", rule="x " * 200 + "formatter")[0], 2)
        self.assertEqual(self.add("--scope", "global", rule="Run · formatter")[0], 2)
        self.assertEqual(
            self.add("--scope", "global", rule="formatter token=abcdef1234567890abcdef")[0], 2
        )
        self.assertEqual(self.add("--scope", "ticket:ABC-1")[0], 2)
        self.assertEqual(self.add("--scope", "repo:global")[0], 2)
        self.assertEqual(self.add("--scope", "repo:ABC-12")[0], 2)
        self.assertEqual(self.add("--scope", "topic:Bad_Slug", "--triggers", "x1")[0], 2)
        self.assertEqual(self.add("--scope", "nonsense")[0], 2)

    def test_line_cap_and_duplicate(self) -> None:
        self.assertEqual(self.add("--scope", "global")[0], 0)
        code, out = self.add("--scope", "global")
        self.assertEqual(code, 0)
        self.assertIn("already present", out)
        self.assertEqual(self.get("rules/global.md").count("\n- "), 1)
        self.set_config("[caps]\nscoped_rule_lines = 1\n")
        self.assertEqual(self.add("--scope", "repo:app")[0], 0)
        code, out = self.add("--scope", "repo:app", rule="Run the formatter again please")
        self.assertEqual(code, 2)
        self.assertIn("cap 1", out)

    def test_global_char_cap(self) -> None:
        self.set_config("[caps]\nglobal_rules_chars = 20\n")
        code, out = self.add("--scope", "global")
        self.assertEqual(code, 2)
        self.assertIn("global rules would be", out)

    def test_new_topic_needs_triggers_and_keeps_them(self) -> None:
        code, out = self.add("--scope", "topic:fmt")
        self.assertEqual((code, "give its triggers" in out), (2, True))
        code, out = self.add("--scope", "topic:fmt", "--triggers", "formatter, lint")
        self.assertEqual(code, 0, out)
        text = self.get("rules/topics/fmt.md")
        self.assertIn("triggers: formatter, lint", text)
        self.assertEqual(self.add("--scope", "global", "--triggers", "x1")[0], 2)

    def test_injected_rule_has_no_provenance(self) -> None:
        self.add("--scope", "global")
        out = self.juno("rule", "list")[1]
        self.assertIn("rules/global.md", out)
        self.assertIn("1. ", out)


class ListAndEdit(WriteCase):
    def setUp(self) -> None:
        super().setUp()
        self.prompt("rather drop the formatter rule, it is stale and wrong now", "pid00002")
        self.put(
            "rules/global.md",
            "# Rules: global\n\n- 2026-01-01 · Run the formatter first · src: unquoted\n"
            "- 2026-01-02 · Keep tests small · src: unquoted\n",
        )

    def test_list_is_read_only_and_works_for_subagents(self) -> None:
        code, out = self.juno("rule", "list", env={"CLAUDE_CODE_AGENT_ID": "a1"})
        self.assertEqual(code, 0)
        self.assertIn("1. 2026-01-01 · Run the formatter first", out)
        code, out = self.juno("rule", "list", "--json")
        self.assertEqual(json.loads(out)["files"][0]["scope"], "global")

    def test_remove_with_quote(self) -> None:
        code, out = self.juno(
            "rule",
            "remove",
            "--scope",
            "global",
            "--quote",
            "drop the formatter rule, it is stale",
            "1",
        )
        self.assertEqual(code, 0, out)
        self.assertNotIn("Run the formatter first", self.get("rules/global.md"))
        self.assertIn("Keep tests small", self.get("rules/global.md"))
        self.assertEqual(
            self.juno("rule", "remove", "--scope", "global", "--quote", "x", "9")[0], 2
        )

    def test_remove_quote_must_relate_to_the_rule(self) -> None:
        code, out = self.juno(
            "rule", "remove", "--scope", "global", "--quote", "it is stale and wrong now", "2"
        )
        self.assertEqual((code, "meaningful word" in out), (2, True))

    def test_move_between_scopes(self) -> None:
        self.put(
            "rules/repos/app.md",
            "# Rules: repo:app\n\n- 2026-01-01 · Keep scripts quiet · src: unquoted\n",
        )
        self.prompt("move the quiet scripts rule from the app to global scope", "pid00003")
        code, out = self.juno(
            "rule",
            "move",
            "--from",
            "repo:app",
            "--to",
            "global",
            "--quote",
            "move the quiet scripts rule from the app to global",
            "1",
        )
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(self.vp("rules/repos/app.md")))
        self.assertIn("Keep scripts quiet", self.get("rules/global.md"))

    def test_triggers_added_to_a_topic(self) -> None:
        self.put(
            "rules/topics/fmt.md",
            "# Rules: topic:fmt\ntriggers: lint\n\n- 2026-01-01 · Rule · src: unquoted\n",
        )
        self.prompt("add the prettier trigger to the fmt topic", "pid00004")
        code, out = self.juno(
            "rule",
            "triggers",
            "--scope",
            "topic:fmt",
            "--add",
            "prettier",
            "--quote",
            "add the prettier trigger to the fmt topic",
        )
        self.assertEqual(code, 0, out)
        self.assertIn("triggers: lint, prettier", self.get("rules/topics/fmt.md"))

    def test_legacy_ticket_file_is_listed_with_a_note(self) -> None:
        self.put("rules/ABC-12.md", "# Rules\n\n- 2026-01-01 · Old rule · src: unquoted\n")
        out = self.juno("rule", "list")[1]
        self.assertIn("legacy ticket rules", out)


class Candidates(WriteCase):
    CAND = "r1a2b3c"

    def setUp(self) -> None:
        super().setUp()
        self.prompt(QUOTE, "pid00002")
        self.seed_queue(
            queues.RULES,
            [
                {
                    "id": self.CAND,
                    "rule": RULE,
                    "quote": QUOTE,
                    "scope": "global",
                    "triggers": [],
                    "pid": "pid00002",
                    "sid": self.sid,
                    "seg8": self.sid[:8],
                    "status": "pending",
                    "t": 1.0,
                }
            ],
        )

    def test_pending_lists_the_candidate(self) -> None:
        code, out = self.juno("rule", "pending")
        self.assertEqual(code, 0)
        self.assertIn(self.CAND, out)
        self.assertEqual(json.loads(self.juno("rule", "pending", "--json")[1])[0]["id"], self.CAND)

    def test_accept_needs_the_id_in_a_typed_prompt(self) -> None:
        code, out = self.juno("rule", "accept", self.CAND)
        self.assertEqual(code, 2)
        self.assertIn("did not choose r1a2b3c", out)
        self.prompt(f"yes {self.CAND} please", "pid00003")
        code, out = self.juno("rule", "accept", self.CAND)
        self.assertEqual(code, 0, out)
        self.assertIn(RULE, self.get("rules/global.md"))
        self.assertEqual(self.queue_status(queues.RULES), {self.CAND: "accepted"})

    def test_accept_by_chosen_answer_label(self) -> None:
        self.answer(f"{self.CAND} · global · {RULE}")
        self.assertEqual(self.juno("rule", "accept", self.CAND)[0], 0)

    def test_quote_is_reverified_against_the_origin_ledger(self) -> None:
        items = queues.read(self.cfg, queues.RULES)
        items[0]["quote"] = "always run the linter before you commit anything"
        self.seed_queue(queues.RULES, items)
        self.prompt(f"yes {self.CAND}", "pid00003")
        code, out = self.juno("rule", "accept", self.CAND)
        self.assertEqual(code, 2)
        self.assertIn("no longer a verbatim part", out)

    def test_reject_and_unknown_ids(self) -> None:
        self.assertEqual(self.juno("rule", "accept", "nonsense")[0], 2)
        self.assertEqual(self.juno("rule", "reject", "r000000")[0], 2)
        code, out = self.juno("rule", "reject", self.CAND)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.queue_status(queues.RULES), {self.CAND: "rejected"})
        self.assertEqual(self.juno("rule", "reject", self.CAND)[0], 2)

    def test_accept_refused_in_a_subagent(self) -> None:
        self.prompt(f"yes {self.CAND}", "pid00003")
        code, _ = self.juno("rule", "accept", self.CAND, env={"CLAUDE_CODE_AGENT_ID": "x"})
        self.assertEqual(code, 2)
        self.assertEqual(self.queue_status(queues.RULES), {self.CAND: "pending"})


if __name__ == "__main__":
    unittest.main()
