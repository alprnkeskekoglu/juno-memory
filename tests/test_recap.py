import datetime
import json
import os
import unittest

from juno.core import recap
from tests.support import SandboxCase

TODAY = datetime.date(2026, 10, 19)


def card(date: str, time: str, key: str, label: str, seg: str, body: str = "") -> str:
    return f"## {date} {time} · {key} · {label} · {seg}\n{body}\n"


def task(
    key: str, status: str = "in-progress", title: str = "A title", updated: str = "2026-10-18"
) -> str:
    return (
        f"---\njira: {key}\ntitle: {title}\nstatus: {status}\nrepos: [webapp]\n"
        f"created: 2026-10-01\nupdated: {updated}\n---\n## Notes\n"
    )


HANDOFFS = {
    "handoffs/ABC-1.md": card(
        "2026-10-17",
        "10:00",
        "ABC-1",
        "in window",
        "aaaa1111",
        "Did the thing\n**Decision:** pick B\n",
    )
    + card("2026-09-01", "10:00", "ABC-1", "too old", "bbbb2222"),
    "handoffs/ABC-2.md": card(
        "2026-10-10", "10:00", "ABC-2", "other", "cccc3333", "auto: true\nOther work\n"
    ),
    "handoffs/repo.md": card("2026-10-12", "10:00", "-", "no ticket", "dddd4444", "Repo work\n"),
    "handoffs/webapp.md": card(
        "2026-10-18", "09:00", "-", "repo card", "eeee5555", "Repos: webapp\nRepo level\n"
    ),
}
TASKS = {
    "tasks/ABC-1.md": task("ABC-1", title="Alpha"),
    "tasks/ABC-2.md": task("ABC-2", title="Beta", updated="2026-08-01"),
}


class Pure(unittest.TestCase):
    def test_deterministic_and_content(self) -> None:
        a = recap.recap(HANDOFFS, TASKS, TODAY, 7)
        self.assertEqual(a, recap.recap(HANDOFFS, TASKS, TODAY, 7))
        self.assertTrue(a.startswith("# Juno recap: 2026-10-12 to 2026-10-19 (7 days)"))
        self.assertIn("## ABC-1 · Alpha", a)
        self.assertIn("in window · Did the thing", a)
        self.assertIn("pick B", a)
        self.assertIn("Status: updated 2026-10-18, status now in-progress", a)
        self.assertNotIn("too old", a)
        self.assertNotIn("## ABC-2", a)
        self.assertIn("repo: webapp", a)

    def test_ticket_filter_and_empty(self) -> None:
        only = recap.recap(HANDOFFS, TASKS, TODAY, 30, "abc-2")
        self.assertIn("## ABC-2", only)
        self.assertNotIn("## ABC-1", only)
        self.assertIn("Other work", only)
        self.assertIn("No activity", recap.recap(HANDOFFS, TASKS, TODAY, 1, "ABC-2"))
        self.assertTrue(only.endswith("\n"))

    def test_text_is_made_inert(self) -> None:
        files = {
            "handoffs/ABC-1.md": card(
                "2026-10-17",
                "10:00",
                "ABC-1",
                "l <!-- x --> a",
                "s1",
                "sum [[tasks/SEC-9]] `c`\n**Decision:** use X · [[tasks/SEC-9]] `y`\n",
            )
        }
        out = recap.recap(files, {}, TODAY, 30)
        for bad in ("<!--", "-->", "[[tasks/SEC-9]]", "`"):
            self.assertNotIn(bad, out)
        self.assertIn("use X &middot; &#91;&#91;tasks/SEC-9&#93;&#93;", out)
        self.assertIn("[[handoffs/ABC-1]]", out)

    def test_assistant_name_in_the_title(self) -> None:
        self.assertTrue(recap.recap({}, {}, TODAY, 7, None, "Atlas").startswith("# Atlas recap"))


class Cli(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)

    def write(self, rel: str, text: str) -> None:
        path = os.path.join(self.sb.vault, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def today_card(self, key: str, label: str, body: str = "") -> str:
        stamp = datetime.date.today().isoformat()
        return card(stamp, "10:00", key, label, "aaaa1111", body)

    def test_recap_lists_todays_card_grouped_by_ticket(self) -> None:
        self.write("handoffs/ABC-1.md", "# H\n\n" + self.today_card("ABC-1", "fresh", "Wrote it\n"))
        code, out = self.sb.cli("recap")
        self.assertEqual(code, 0, out)
        self.assertIn("## ABC-1", out)
        self.assertIn("fresh · Wrote it", out)

    def test_retired_private_and_odd_names_are_left_out(self) -> None:
        self.write("handoffs/ABC-1.md", "# H\n\n" + self.today_card("ABC-1", "visible"))
        self.write(
            "handoffs/ABC-2.md",
            "---\nvisibility: private\n---\n# H\n\n" + self.today_card("ABC-2", "secret"),
        )
        self.write("handoffs/a b.md", "# H\n\n" + self.today_card("ABC-3", "spacey"))
        self.write(
            "handoffs/ABC-4.md",
            "---\nstatus: archived\n---\n# H\n\n" + self.today_card("ABC-4", "archived"),
        )
        out = self.sb.cli("recap")[1]
        self.assertIn("visible", out)
        for hidden in ("secret", "spacey", "archived"):
            self.assertNotIn(hidden, out)

    def test_json_week_and_bad_days(self) -> None:
        code, out = self.sb.cli("recap", "--week", "--json")
        data = json.loads(out)
        self.assertEqual((code, data["days"], data["ticket"]), (0, 7, None))
        self.assertIn("No activity", data["markdown"])
        self.assertEqual(self.sb.cli("recap", "--days", "0")[0], 2)


if __name__ == "__main__":
    unittest.main()
