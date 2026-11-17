import json
import unittest

from juno.adapters import queues, writelog
from juno.core import doctor_pinned
from juno.core import doctor_vault as dv
from juno.core.vaultfmt import rule_entries
from tests.support.write import WriteCase

QUOTE = "always reply in Turkish whatever language the logs are in"
FIRST = "- 2026-01-01 · Reply in Turkish · src: unquoted"
SECOND = "- 2026-01-02 · Keep tests small · src: unquoted"
HEAD = "# Rules: global\n\n"
BAD_QUOTE = "I never typed this Turkish sentence anywhere"


class Pin(WriteCase):
    def setUp(self) -> None:
        super().setUp()
        self.prompt(QUOTE + ", it keeps drifting", "pid00002")
        self.put("rules/global.md", f"{HEAD}{FIRST}\n{SECOND}\n")

    def pin(self, verb: str = "pin", quote: str = QUOTE, index: str = "1") -> tuple[int, str]:
        return self.juno("rule", verb, "--scope", "global", "--quote", quote, index)

    def wid(self, out: str) -> str:
        return out.split("(write ")[1].split(";")[0]

    def test_pin_marks_the_line_and_prints_a_write_id(self) -> None:
        code, out = self.pin()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.get("rules/global.md"), f"{HEAD}{FIRST} · pinned\n{SECOND}\n")
        self.assertRegex(out, r"write w-[0-9a-f]{8}; revert with `juno undo w-[0-9a-f]{8}`")
        events = [e for e in self.events() if e["ev"] == "rule_pinned"]
        self.assertEqual(events[0]["scope"], "global")

    def test_unpin_removes_the_mark_and_logs_the_event(self) -> None:
        self.pin()
        code, out = self.pin("unpin")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.get("rules/global.md"), f"{HEAD}{FIRST}\n{SECOND}\n")
        unpinned = [e["scope"] for e in self.events() if e["ev"] == "rule_unpinned"]
        self.assertEqual(unpinned, ["global"])

    def test_unanchored_quote_is_refused_and_file_unchanged(self) -> None:
        before = self.get("rules/global.md")
        code, out = self.pin(quote=BAD_QUOTE)
        self.assertEqual(code, 2)
        self.assertIn("Juno: refused: quote is not a verbatim part", out)
        self.assertEqual(self.get("rules/global.md"), before)
        code, out = self.pin("unpin", quote=BAD_QUOTE)
        self.assertEqual(code, 2)
        self.assertEqual(self.get("rules/global.md"), before)

    def test_undo_reverts_the_pin_exactly(self) -> None:
        before = self.get("rules/global.md")
        code, out = self.pin()
        self.assertEqual(code, 0, out)
        wid = self.wid(out)
        self.assertIn(wid, [r["id"] for r in writelog.read(self.cfg)])
        code, undone = self.juno("undo", wid)
        self.assertEqual(code, 0, undone)
        self.assertEqual(self.get("rules/global.md"), before)
        pinned = [e.pinned for e in rule_entries(self.get("rules/global.md"))]
        self.assertEqual(pinned, [False, False])

    def test_undo_reverts_an_unpin(self) -> None:
        self.pin()
        pinned = self.get("rules/global.md")
        _, out = self.pin("unpin")
        self.assertEqual(self.juno("undo", self.wid(out))[0], 0)
        self.assertEqual(self.get("rules/global.md"), pinned)

    def test_repeat_is_a_noop_without_a_write(self) -> None:
        self.pin()
        writes = len(writelog.read(self.cfg))
        code, out = self.pin()
        self.assertEqual(code, 0)
        self.assertIn("already pinned", out)
        self.prompt("always keep tests small and fast", "pid00003")
        code, out = self.pin("unpin", index="2", quote="always keep tests small and fast")
        self.assertEqual(code, 0, out)
        self.assertIn("not pinned", out)
        self.assertEqual(len(writelog.read(self.cfg)), writes)

    def test_malformed_line_and_bad_index_are_refused(self) -> None:
        self.put("rules/global.md", f"{HEAD}- hand written line about Turkish\n")
        code, out = self.pin()
        self.assertEqual(code, 2)
        self.assertIn("not a well-formed rule line", out)
        self.assertEqual(self.pin(index="9")[0], 2)

    def test_pin_is_refused_in_a_subagent(self) -> None:
        code, _ = self.juno(
            "rule",
            "pin",
            "--scope",
            "global",
            "--quote",
            QUOTE,
            "1",
            env={"CLAUDE_CODE_AGENT_ID": "a1"},
        )
        self.assertEqual(code, 2)
        self.assertNotIn("pinned", self.get("rules/global.md"))

    def test_add_with_pin_writes_a_pinned_line(self) -> None:
        code, out = self.juno(
            "rule",
            "add",
            "--scope",
            "global",
            "--quote",
            QUOTE,
            "--pin",
            "Answer in Turkish always",
        )
        self.assertEqual(code, 0, out)
        entries = rule_entries(self.get("rules/global.md"))
        self.assertEqual([e.pinned for e in entries], [False, False, True])
        self.assertEqual(entries[2].text, "Answer in Turkish always")

    def test_add_without_pin_is_not_pinned(self) -> None:
        self.juno("rule", "add", "--scope", "global", "--quote", QUOTE, "Answer in Turkish always")
        self.assertNotIn("pinned", self.get("rules/global.md"))

    def test_add_pin_of_an_existing_rule_pins_it_with_a_write_id(self) -> None:
        argv = ("rule", "add", "--scope", "global", "--quote", QUOTE, "--pin", "Reply in Turkish")
        code, out = self.juno(*argv)
        self.assertEqual(code, 0, out)
        self.assertIn("already present", out)
        self.assertEqual(self.get("rules/global.md"), f"{HEAD}{FIRST} · pinned\n{SECOND}\n")
        self.assertIn("revert with `juno undo w-", out)
        code, out = self.juno(*argv)
        self.assertEqual(code, 0)
        self.assertEqual(self.get("rules/global.md").count("pinned"), 1)

    def test_move_keeps_the_pin_and_dedupes_by_text(self) -> None:
        self.pin()
        move = ("rule", "move", "--from", "global", "--to", "repo:app", "--quote", QUOTE, "1")
        code, out = self.juno(*move)
        self.assertEqual(code, 0, out)
        moved = rule_entries(self.get("rules/repos/app.md"))
        self.assertEqual([(e.text, e.pinned) for e in moved], [("Reply in Turkish", True)])
        self.assertNotIn("Turkish", self.get("rules/global.md"))
        self.put("rules/global.md", f"{HEAD}{FIRST}\n")
        code, out = self.juno(*move)
        self.assertEqual(code, 2)
        self.assertIn("already present", out)

    def test_remove_deletes_a_pinned_rule(self) -> None:
        self.pin()
        code, out = self.juno("rule", "remove", "--scope", "global", "--quote", QUOTE, "1")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.get("rules/global.md"), f"{HEAD}{SECOND}\n")

    def test_list_marks_pinned_rules_and_json_has_a_pinned_list(self) -> None:
        self.pin()
        _, out = self.juno("rule", "list")
        self.assertIn("  1. [pinned] 2026-01-01 · Reply in Turkish · src: unquoted", out)
        self.assertIn("  2. 2026-01-02 · Keep tests small", out)
        self.assertNotIn("· pinned", out)
        entry = json.loads(self.juno("rule", "list", "--json")[1])["files"][0]
        self.assertEqual(entry["pinned"], [True, False])
        self.assertEqual(len(entry["rules"]), 2)
        self.assertTrue(all(isinstance(r, str) for r in entry["rules"]))


class Accept(WriteCase):
    def seed(self, rule: str) -> None:
        self.prompt(QUOTE, "pid00002")
        self.prompt("yes r1a2b3c please", "pid00003")
        self.seed_queue(
            queues.RULES,
            [
                {
                    "id": "r1a2b3c",
                    "rule": rule,
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

    def test_accept_never_pins(self) -> None:
        self.seed("Reply in Turkish")
        code, out = self.juno("rule", "accept", "r1a2b3c")
        self.assertEqual(code, 0, out)
        entries = rule_entries(self.get("rules/global.md"))
        self.assertEqual([(e.text, e.pinned) for e in entries], [("Reply in Turkish", False)])

    def test_candidate_text_cannot_smuggle_the_pin_mark(self) -> None:
        self.seed("Reply in Turkish · pinned")
        code, _ = self.juno("rule", "accept", "r1a2b3c")
        self.assertTrue(all(not e.pinned for e in rule_entries(self.get("rules/global.md"))))
        self.assertEqual(code, 2)


class Lint(WriteCase):
    def test_vault_doctor_accepts_a_pinned_line(self) -> None:
        files = {"global.md": f"{HEAD}{FIRST} · pinned\n"}
        out = dv.rule_checks(files, 80, 20, "none")
        self.assertEqual([c for c in out if c[0] == "FAIL"], [])

    def test_pinned_rules_over_the_cap_warn_naming_each_rule(self) -> None:
        files = {
            "global.md": f"{HEAD}{FIRST} · pinned\n{SECOND}\n",
            "repos/app.md": "- 2026-01-03 · Use the shared lint config · src: unquoted · pinned\n",
        }
        out = doctor_pinned.pinned_checks(files, 20)
        self.assertEqual([c[0] for c in out], ["WARN"])
        self.assertIn("global #1", out[0][2])
        self.assertIn("repo:app #1", out[0][2])
        self.assertNotIn("global #2", out[0][2])
        self.assertEqual(doctor_pinned.pinned_checks(files, 800)[0][0], "PASS")
        self.assertEqual(doctor_pinned.pinned_checks({"global.md": f"{HEAD}{SECOND}\n"}, 1), [])

    def test_cli_doctor_warns_about_oversized_pinned_rules(self) -> None:
        self.put("rules/global.md", f"{HEAD}{FIRST} · pinned\n")
        self.set_config("[caps]\npinned_rules_chars = 10\n")
        _, out = self.juno("doctor", "--json", *self.sb.install_args())
        rows = [c for c in json.loads(out)["checks"] if c["name"] == "pinned rules"]
        self.assertEqual([r["status"] for r in rows], ["WARN"])
        self.assertIn("global #1", rows[0]["detail"])

    def test_cli_doctor_fails_when_pinned_cap_reaches_the_hook_cap(self) -> None:
        self.set_config("[caps]\npinned_rules_chars = 9500\n")
        _, out = self.juno("doctor", "--json", *self.sb.install_args())
        rows = [c for c in json.loads(out)["checks"] if c["name"] == "pinned cap"]
        self.assertEqual([r["status"] for r in rows], ["FAIL"])

    def test_cli_doctor_passes_the_default_pinned_cap(self) -> None:
        _, out = self.juno("doctor", "--json", *self.sb.install_args())
        rows = [c for c in json.loads(out)["checks"] if c["name"] == "pinned cap"]
        self.assertEqual([r["status"] for r in rows], ["PASS"])


if __name__ == "__main__":
    unittest.main()
