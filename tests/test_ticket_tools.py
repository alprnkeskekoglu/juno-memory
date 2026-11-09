import json
import time
import unittest

from juno.adapters import ledgerio
from juno.core import ledger, routing, toolrec
from juno.core.detect import Tickets
from juno.core.ledger import Segment
from tests.support.hooks import SID, HookCase
from tests.support.write import WriteCase

TICKETS = Tickets(("ABC", "DEF"))
RULES = ledger.Rules(tickets=TICKETS)
EDIT = "mcp__atlassian__editJiraIssue"
CREATE = "mcp__atlassian__createJiraIssue"


class ToolKeys(unittest.TestCase):
    def keys(self, tool: str, tool_in: dict, response: object = None) -> list[str]:
        return toolrec.tool_ticket_keys(tool, tool_in, response, TICKETS)

    def test_input_fields_are_generic(self) -> None:
        for field in ("issueIdOrKey", "issueKey", "key", "issue_key"):
            self.assertEqual(self.keys("mcp__any__tool", {field: "abc-12"}), ["ABC-12"])

    def test_only_mcp_tools_and_only_whole_prefixed_keys(self) -> None:
        self.assertEqual(self.keys("Bash", {"key": "ABC-1"}), [])
        self.assertEqual(self.keys(EDIT, {"issueIdOrKey": "XYZ-1"}), [])
        self.assertEqual(self.keys(EDIT, {"issueIdOrKey": "see ABC-1 and more"}), [])
        self.assertEqual(self.keys(EDIT, {"summary": "ABC-1"}), [])
        self.assertEqual(toolrec.tool_ticket_keys(EDIT, {"key": "ABC-1"}, None, Tickets()), [])

    def test_create_response_key(self) -> None:
        body = json.dumps({"id": "10", "key": "DEF-7", "self": "https://x"})
        self.assertEqual(self.keys(CREATE, {"summary": "s"}, [{"type": "text", "text": body}]), ["DEF-7"])
        self.assertEqual(self.keys(CREATE, {}, {"content": [{"type": "text", "text": body}]}), ["DEF-7"])
        self.assertEqual(self.keys(CREATE, {}, {"key": "DEF-8"}), ["DEF-8"])
        self.assertEqual(self.keys(CREATE, {}, body), ["DEF-7"])
        self.assertEqual(self.keys("mcp__a__searchJira", {}, body), [])

    def test_response_scan_is_capped(self) -> None:
        late = " " * (toolrec.RESPONSE_SCAN_CHARS + 10) + '{"key": "ABC-9"}'
        self.assertEqual(self.keys(CREATE, {}, late), [])


class Fold(unittest.TestCase):
    def test_main_tool_keys_become_segment_tickets(self) -> None:
        s = Segment(SID, RULES)
        s.apply({"ev": "tool", "name": EDIT, "keys": ["ABC-5"]})
        self.assertEqual(set(s.tickets), {"ABC-5"})
        self.assertEqual((s.tickets["ABC-5"], s.main_tickets["ABC-5"]), (1, 1))
        self.assertEqual(routing.handoff_targets(s)[0]["path"], "handoffs/ABC-5.md")
        self.assertEqual(routing.handoff_targets(s)[0]["key"], "ABC-5")

    def test_subagent_and_foreign_keys_are_not_counted(self) -> None:
        s = Segment(SID, RULES)
        s.apply({"ev": "tool", "agent": True, "keys": ["ABC-5"]})
        s.apply({"ev": "tool", "keys": ["XYZ-1", 7]})
        self.assertEqual(set(s.tickets), set())
        self.assertEqual(routing.handoff_targets(s)[0]["key"], "-")

    def test_no_keys_anywhere_keeps_the_old_behaviour(self) -> None:
        s = Segment(SID, RULES)
        s.apply({"ev": "tool", "repos": [["app", None]]})
        target = routing.handoff_targets(s)[0]
        self.assertEqual((target["path"], target["key"], target["tickets"]), ("handoffs/app.md", "-", []))

    def test_prefixes_unset_ignores_keys(self) -> None:
        s = Segment(SID)
        s.apply({"ev": "tool", "keys": ["ABC-5"]})
        self.assertEqual(set(s.tickets), set())

    def test_several_keys_follow_the_existing_rank(self) -> None:
        s = Segment(SID, RULES)
        s.apply({"ev": "tool", "keys": ["ABC-1"]})
        s.apply({"ev": "tool", "keys": ["ABC-2"]})
        s.apply({"ev": "tool", "keys": ["ABC-2"]})
        self.assertEqual(routing.handoff_targets(s)[0]["key"], "ABC-2")

    def test_oversized_keys_shrink_instead_of_failing(self) -> None:
        rec = {"ev": "tool", "keys": [f"ABC-{i}" for i in range(5)], "repos": [[f"r{i}", None] for i in range(400)]}
        _, data, changed = ledger.encode_capped(rec)
        self.assertTrue(changed)
        self.assertLessEqual(len(data), ledger.MAX_LINE_BYTES)


class Hook(HookCase):
    def payload_for(self, tool: str, tool_in: dict, response: object = None, **extra: object) -> dict:
        return self.payload(
            "PostToolUse", tool_name=tool, tool_input=tool_in, tool_response=response, **extra
        )

    def tool_rows(self) -> list[dict]:
        return [r for r in self.ledger() if r.get("ev") == "tool"]

    def test_mcp_input_key_is_recorded_and_counted(self) -> None:
        self.hook("post_tool_use", self.payload_for(EDIT, {"issueIdOrKey": "ABC-5", "fields": {}}))
        self.assertEqual(self.tool_rows()[-1]["keys"], ["ABC-5"])
        seg = ledgerio.fold(self.cfg(), SID)
        self.assertEqual(routing.card_targets(seg)[0]["key"], "ABC-5")

    def test_create_response_key_is_recorded(self) -> None:
        body = [{"type": "text", "text": '{"id":"1","key":"DEF-3"}'}]
        self.hook("post_tool_use", self.payload_for(CREATE, {"summary": "x"}, body))
        self.assertEqual(self.tool_rows()[-1]["keys"], ["DEF-3"])

    def test_subagent_tool_call_is_not_counted(self) -> None:
        self.hook(
            "post_tool_use", self.payload_for(EDIT, {"issueIdOrKey": "ABC-5"}, agent_id="sub1")
        )
        self.assertNotIn("keys", self.tool_rows()[-1])
        self.assertEqual(set(ledgerio.fold(self.cfg(), SID).tickets), set())

    def test_non_prefix_key_is_ignored(self) -> None:
        self.hook("post_tool_use", self.payload_for(EDIT, {"issueIdOrKey": "XYZ-5"}))
        self.assertNotIn("keys", self.tool_rows()[-1])

    def test_plain_tools_record_no_keys(self) -> None:
        self.hook("post_tool_use", self.payload_for("Bash", {"command": "echo ABC-1"}, {}))
        self.assertNotIn("keys", self.tool_rows()[-1])

    def cfg(self):  # noqa: ANN201
        from juno import config

        return config.load(self.env)


class Card(WriteCase):
    TICKETS = True

    def tool(self, keys: list[str] | None = None, repo: str | None = None, **extra: object) -> None:
        rec: dict = {"ev": "tool", "name": EDIT, **extra}
        if keys:
            rec["keys"] = keys
        if repo:
            rec["repos"] = [[repo, None]]
        ledgerio.append(self.cfg, self.sid, rec)

    def card(self, *flags: str) -> tuple[int, str]:
        return self.juno(
            "card", "--learned", "none", *flags, "Did the work here.", "Next is the deploy."
        )

    def refused(self, *flags: str) -> str:
        code, out = self.card(*flags)
        self.assertEqual(code, 2, out)
        self.assertTrue(out.startswith("Juno: refused:"), out)
        self.assertEqual(len(out.strip().splitlines()), 1, out)
        return out

    def test_tool_key_routes_the_card_to_the_ticket_file(self) -> None:
        self.tool(["ABC-5"], repo="app")
        code, out = self.card()
        self.assertEqual(code, 0, out)
        text = self.get("handoffs/ABC-5.md")
        self.assertIn(f"· ABC-5 · work · {self.sid[:8]}", text)
        self.assertIn("Tickets: ABC-5", text)
        self.assertEqual(self.get("handoffs/app.md"), "")

    def test_no_keys_anywhere_is_unchanged(self) -> None:
        self.tool(repo="app")
        self.assertEqual(self.card()[0], 0)
        text = self.get("handoffs/app.md")
        self.assertIn(f"· - · work · {self.sid[:8]}", text)
        self.assertIn("Tickets: -", text)

    def test_links_key_becomes_a_ticket(self) -> None:
        self.tool(repo="app")
        code, out = self.card("--links", "[[app]], [[ABC-12]]")
        self.assertEqual(code, 0, out)
        text = self.get("handoffs/ABC-12.md")
        self.assertIn(f"· ABC-12 · work · {self.sid[:8]}", text)
        self.assertIn("Tickets: ABC-12", text)
        self.assertIn("Repos: app", text)
        seen = [e for e in self.events() if e.get("ev") == "keys"]
        self.assertEqual(seen[0]["keys"], ["ABC-12"])

    def test_links_with_foreign_prefix_are_ignored(self) -> None:
        self.tool(repo="app")
        self.assertEqual(self.card("--links", "[[XYZ-12]]")[0], 0)
        self.assertIn("Tickets: -", self.get("handoffs/app.md"))

    def test_tickets_override_accepts_seen_keys(self) -> None:
        self.tool(["ABC-5"], repo="app")
        self.tool(["ABC-6"])
        self.tool(["ABC-6"])
        code, out = self.card("--tickets", "abc-5,ABC-6")
        self.assertEqual(code, 0, out)
        text = self.get("handoffs/ABC-6.md")
        self.assertIn("Tickets: ABC-5, ABC-6", text)

    def test_tickets_override_accepts_a_link_key(self) -> None:
        self.tool(repo="app")
        code, out = self.card("--tickets", "ABC-9", "--links", "[[ABC-9]]")
        self.assertEqual(code, 0, out)
        self.assertIn("Tickets: ABC-9", self.get("handoffs/ABC-9.md"))

    def test_tickets_override_refuses_unseen_and_bad_keys(self) -> None:
        self.tool(["ABC-5"], repo="app")
        self.assertIn("not seen", self.refused("--tickets", "ABC-77"))
        self.assertIn("not a ticket key", self.refused("--tickets", "XYZ-5"))
        self.assertIn("not a ticket key", self.refused("--tickets", "ABC-5,nonsense"))
        self.assertIn("empty", self.refused("--tickets", " , "))
        self.assertEqual(self.get("handoffs/ABC-5.md"), "")

    def test_repos_override(self) -> None:
        self.tool(repo="app")
        self.put("projects/other.md", "# other\n")
        code, out = self.card("--repos", "app,other")
        self.assertEqual(code, 0, out)
        self.assertIn("Repos: app, other", self.get("handoffs/app.md"))

    def test_repos_override_refuses_unknown_repos(self) -> None:
        self.tool(repo="app")
        self.assertIn("no vault profile", self.refused("--repos", "ghost"))
        self.assertIn("is empty", self.refused("--repos", ","))
        self.assertEqual(self.get("handoffs/ghost.md"), "")

    def test_without_prefixes_tickets_flag_is_refused(self) -> None:
        self.set_config('[jira]\nprefixes = []\n')
        self.tool(repo="app")
        self.assertIn("prefixes", self.refused("--tickets", "ABC-1"))

    def test_second_card_replaces_the_first(self) -> None:
        self.tool(["ABC-5"], repo="app")
        self.assertEqual(self.card()[0], 0)
        self.assertEqual(self.card()[0], 0)
        self.assertEqual(self.get("handoffs/ABC-5.md").count(f"· {self.sid[:8]}"), 1)


if __name__ == "__main__":
    unittest.main()
