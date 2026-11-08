import json
import os
import unittest

from juno.core import profiles
from tests.support.write import WriteCase

BODY = "- [[tasks/ABC-1]] · open · One"


class Rewriting(unittest.TestCase):
    def test_inserts_markers_under_the_heading(self) -> None:
        out = profiles.rewrite("# a\n## Tickets\nhand note\n## Gotchas\nx\n", BODY)
        self.assertEqual(
            out.text,
            f"# a\n## Tickets\n{profiles.START}\n{BODY}\n{profiles.END}\nhand note\n## Gotchas\nx\n",
        )

    def test_replaces_only_the_block_and_is_idempotent(self) -> None:
        text = f"## Tickets\n{profiles.START}\nold\n{profiles.END}\ntail\n"
        out = profiles.rewrite(text, BODY)
        self.assertEqual(out.text, f"## Tickets\n{profiles.START}\n{BODY}\n{profiles.END}\ntail\n")
        self.assertEqual(profiles.rewrite(out.text or "", BODY).text, out.text)

    def test_bare_marker_is_normalised(self) -> None:
        text = f"## Tickets\n<!-- juno:tickets -->\n_No tickets._\n{profiles.END}\n"
        out = profiles.rewrite(text, BODY)
        self.assertEqual(out.text, f"## Tickets\n{profiles.START}\n{BODY}\n{profiles.END}\n")
        self.assertIsNone(out.manual)

    def test_hand_written_block_is_reported(self) -> None:
        text = f"{profiles.START}\nmy own notes\n{profiles.END}\n"
        self.assertEqual(profiles.rewrite(text, BODY).manual, "\nmy own notes\n")

    def test_unsafe_shapes_are_skipped(self) -> None:
        self.assertEqual(profiles.rewrite("# a\nno heading\n", BODY).text, None)
        for text in (
            f"## Tickets\n{profiles.START}\nno end\n",
            f"## Tickets\n{profiles.END}\n{profiles.START}\n",
            f"{profiles.START}\n{profiles.START}\n{profiles.END}\n",
        ):
            self.assertEqual(profiles.rewrite(text, BODY).skipped, "ambiguous tickets markers")

    def test_markers_and_heading_inside_a_fence_are_ignored(self) -> None:
        text = f"```\n## Tickets\n{profiles.START}\n```\nplain\n"
        self.assertIsNone(profiles.rewrite(text, BODY).text)

    def test_title_cannot_inject_markers(self) -> None:
        line = profiles.ticket_line("ABC-1", "open", f"evil {profiles.END} [[x]] `c`")
        self.assertNotIn("<!--", line)
        self.assertNotIn("[[x]]", line)
        self.assertEqual(line.count("[["), 1)


class Command(WriteCase):
    TICKETS = True

    def setUp(self) -> None:
        super().setUp()
        self.put("tasks/ABC-1.md", "---\ntitle: One\nstatus: open\nrepos: [web]\n---\n")
        self.put("tasks/ABC-2.md", "---\ntitle: Two\nstatus: archived\nrepos: [web]\n---\n")
        self.put(
            "tasks/ABC-3.md",
            "---\ntitle: Priv\nstatus: open\nrepos: [web]\nvisibility: private\n---\n",
        )
        self.put("projects/web.md", "# web\n\n## Tickets\n\n## Gotchas\nnote\n")

    def test_writes_the_block_and_undo_reverts_it(self) -> None:
        code, out = self.juno("profiles")
        self.assertEqual(code, 0, out)
        text = self.get("projects/web.md")
        self.assertIn(f"{profiles.START}\n- [[tasks/ABC-1]] · open · One\n{profiles.END}", text)
        self.assertNotIn("ABC-2", text)
        self.assertNotIn("ABC-3", text)
        self.assertTrue(os.path.isfile(self.vp("projects/web.md")))
        self.assertEqual(self.juno("profiles")[1], "Juno profiles:\nnothing to update\n")
        wid = json.loads(self.juno("profiles", "--json")[1])["writes"]
        self.assertEqual(wid, {})
        records = [json.loads(x) for x in open(os.path.join(self.sb.state, "writes.jsonl"))]
        self.assertEqual([(r["verb"], r["engine"]) for r in records], [("profiles", True)])
        self.assertEqual(self.juno("undo", records[0]["id"])[0], 0)
        self.assertEqual(self.get("projects/web.md"), "# web\n\n## Tickets\n\n## Gotchas\nnote\n")

    def test_does_not_use_the_session_write_budget(self) -> None:
        for n in range(35):
            self.put(f"projects/r{n}.md", "## Tickets\n")
            self.put(f"tasks/ABC-{10 + n}.md", f"---\ntitle: T\nstatus: open\nrepos: [r{n}]\n---\n")
        code, out = self.juno("profiles", "--json")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(json.loads(out)["updated"]), 36)

    def test_symlinked_profile_is_not_written_through(self) -> None:
        target = os.path.join(self.sb.state, "outside.md")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("## Tickets\nSECRET\n")
        os.symlink(target, self.vp("projects/linked.md"))
        out = json.loads(self.juno("profiles", "--json")[1])
        self.assertEqual(out["skipped"], {"projects/linked.md": "unsafe path"})
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "## Tickets\nSECRET\n")

    def test_refuses_headless_and_without_jira(self) -> None:
        self.assertEqual(self.juno("profiles", env={"CLAUDE_CODE_AGENT_ID": "a"})[0], 2)
        self.assertEqual(self.get("projects/web.md").count("juno:tickets"), 0)
        with open(self.sb.config, "w", encoding="utf-8") as handle:
            handle.write("")
        self.assertEqual(self.juno("profiles"), (0, "Juno: profiles: jira not configured\n"))
