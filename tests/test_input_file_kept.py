import json
import os

from tests.test_arms import PREREG
from tests.test_backlog_cli import BODY, QUOTE, BacklogCase
from tests.test_maestro_verbs import BLOCK, EV
from tests.test_task_log import HAND, LogCase
from tests.test_writegate_cli import Base

ROW = {"section": "Plan", "key": "a", "line": "first plan line here"}
TICKET = "ticket:ABC-123"


def rows(*items: object) -> str:
    return "\n".join(json.dumps(r) for r in items) + "\n"


def read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


class TaskLogBatch(LogCase):
    def kept(self, text: str, evidence: str | None = TICKET) -> None:
        self.put("tasks/ABC-100.md", HAND)
        path = self.batch(text)
        before = read(path)
        code, out = self.log("--batch-file", path, evidence=evidence)
        self.assertEqual(code, 2, out)
        self.assertEqual(read(path), before)
        self.assertEqual(self.task(), HAND)

    def test_untouched_ticket_evidence_keeps_the_file(self) -> None:
        self.kept(rows(ROW))

    def test_privacy_refused_row_keeps_the_file(self) -> None:
        self.kept(rows(dict(ROW, line="ref 123456789012 failed")))

    def test_bad_json_row_keeps_the_file(self) -> None:
        self.kept(json.dumps(ROW) + "\n{not json}\n")

    def test_duplicate_pair_keeps_the_file(self) -> None:
        self.kept(rows(ROW, dict(ROW, line="again again again")))

    def test_over_limit_file_keeps_the_file(self) -> None:
        self.kept("x" * 17000)

    def test_success_and_unchanged_repeat_consume_the_file(self) -> None:
        self.put("tasks/ABC-100.md", HAND)
        path = self.batch(rows(ROW))
        code, out = self.log("--batch-file", path)
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(path))
        path = self.batch(rows(ROW))
        code, out = self.log("--batch-file", path)
        self.assertEqual((code, "unchanged" in out), (0, True), out)
        self.assertFalse(os.path.exists(path))


class BacklogFiles(BacklogCase):
    def test_add_refused_keeps_the_file_and_success_consumes_it(self) -> None:
        path = self.body_file(BODY)
        args = ("backlog", "add", "--title", "Kept body", "--priority", "p2", "--body-file", path)
        code, out = self.juno(*args, "--quote", "words nobody typed in this session")
        self.assertEqual(code, 2, out)
        self.assertEqual(read(path), BODY.encode())
        code, out = self.juno(*args, "--quote", QUOTE)
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(path))

    def test_add_to_and_edit_refused_keep_the_file_and_success_consumes_it(self) -> None:
        self.assertEqual(self.add()[0], 0)
        slug = "invoice-export-cleanup"
        path = self.body_file("Another line\n", "extra.md")
        add_to = ("backlog", "add-to", "--quote", QUOTE, "--section", "research")
        code, out = self.juno(*add_to, "missing-item", "--body-file", path)
        self.assertEqual(code, 2, out)
        self.assertEqual(read(path), b"Another line\n")
        self.assertEqual(self.juno(*add_to, slug, "--body-file", path)[0], 0)
        self.assertFalse(os.path.exists(path))
        repl = self.body_file("Replaced line\n", "repl.md")
        edit = ("backlog", "edit", slug, "--quote", QUOTE, "--section", "research")
        code, out = self.juno(*edit, "--line", "99", "--replace-file", repl)
        self.assertEqual(code, 2, out)
        self.assertEqual(read(repl), b"Replaced line\n")
        self.assertEqual(self.juno(*edit, "--line", "1", "--replace-file", repl)[0], 0)
        self.assertFalse(os.path.exists(repl))


class RuntimeBlock(Base):
    def test_refused_keeps_the_file_and_success_consumes_it(self) -> None:
        self.touch_repo("app")
        path = self.body_file("lang: php\n")
        self.refused("project", "runtime", *EV, "--block-file", path)
        self.assertEqual(read(path), b"lang: php\n")
        path = self.body_file(BLOCK)
        self.ok("project", "runtime", *EV, "--block-file", path)
        self.assertFalse(os.path.exists(path))


class ArmPrereg(Base):
    def test_refused_keeps_the_file_and_success_consumes_it(self) -> None:
        text = json.dumps(dict(PREREG, min_n=0))
        path = self.body_file(text, "p.json")
        self.refused("arm", "start", "demo", "--prereg", path)
        self.assertEqual(read(path), text.encode())
        path = self.body_file(json.dumps(PREREG), "p.json")
        self.ok("arm", "start", "demo", "--prereg", path)
        self.assertFalse(os.path.exists(path))
