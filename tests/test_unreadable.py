import contextlib
import io
import json
import unittest
from typing import Any
from unittest import mock

from juno import cli
from juno.adapters import store
from juno.core import stale
from tests.support.hooks import HookCase

LABEL = "2026-W45"
KNOW = "---\ntitle: T\nrepos: [hb]\nupdated: 2026-01-01\nsources: [x]\n---\nbody\n"


def flaky(real: Any, bad: dict[str, Exception]) -> Any:
    def read(path: str) -> str:
        for suffix, error in bad.items():
            if path.endswith(suffix):
                raise error
        return str(real(path))

    return read


class Stale(HookCase):
    def run_cli(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(list(argv), self.env)
        return code, buffer.getvalue()

    def bad(self) -> Any:
        self.write("knowledge/ok.md", KNOW)
        self.write("knowledge/bad.md", KNOW)
        self.write("knowledge/worse.md", KNOW)
        errors = {
            "knowledge/bad.md": PermissionError(13, "denied"),
            "knowledge/worse.md": UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
        }
        return mock.patch.object(store, "read_text", flaky(store.read_text, errors))

    def test_json_lists_the_unreadable_files(self) -> None:
        with self.bad():
            code, out = self.run_cli("stale", "--json")
        self.assertEqual(code, 0, out)
        data = json.loads(out)
        self.assertEqual(data["unreadable"], ["knowledge/bad.md", "knowledge/worse.md"])
        self.assertEqual([e["file"] for e in data["entries"]], ["knowledge/ok.md"])

    def test_text_says_so(self) -> None:
        with self.bad():
            code, out = self.run_cli("stale")
        self.assertEqual(code, 0, out)
        self.assertIn(
            "2 files could not be read: knowledge/bad.md, knowledge/worse.md", out.splitlines()[-1]
        )

    def test_clean_scan_has_no_line_and_an_empty_list(self) -> None:
        self.write("knowledge/ok.md", KNOW)
        _, out = self.run_cli("stale")
        self.assertNotIn("could not be read", out)
        self.assertEqual(json.loads(self.run_cli("stale", "--json")[1])["unreadable"], [])

    def test_render_is_pure(self) -> None:
        report = stale.report_of(
            30, __import__("datetime").date(2026, 1, 1), [], False, 0.0, ["a.md"]
        )
        self.assertIn("1 file could not be read: a.md", stale.render(report))


class Report(HookCase):
    def test_weekly_report_names_unreadable_vault_files(self) -> None:
        self.write("knowledge/bad.md", "# Bad\n\n- a line that cannot be read\n")
        self.write("knowledge/ok.md", "# Ok\n\n- a line that can be read\n")
        errors = {"knowledge/bad.md": PermissionError(13, "denied")}
        buffer = io.StringIO()
        with (
            mock.patch.object(store, "read_text", flaky(store.read_text, errors)),
            contextlib.redirect_stdout(buffer),
        ):
            code = cli.main(["report", "--week", LABEL], self.env)
        self.assertEqual(code, 0, buffer.getvalue())
        text = self.read(f"_generated/reports/{LABEL}.md")
        self.assertIn("1 file could not be read: knowledge/bad.md", text)

    def test_clean_report_has_no_such_line(self) -> None:
        self.write("knowledge/ok.md", "# Ok\n\n- a line that can be read\n")
        self.assertEqual(self.sb.cli("report", "--week", LABEL)[0], 0)
        self.assertNotIn("could not be read", self.read(f"_generated/reports/{LABEL}.md"))


if __name__ == "__main__":
    unittest.main()
