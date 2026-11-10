import time

from juno.adapters import cardjob, writelog
from tests.support.write import WriteCase

QUOTE = "this is the first typed prompt"
OLD = "aaaaaaaa-1111-4222-8333-444444444444"
S8 = OLD[:8]
MADE_UP = "some invented words nobody typed"


def stub(seg: str = S8, key: str = "app", excerpt: str = "Shipped the export fix.") -> str:
    return (
        f"## 2026-10-28 09:00 · {key} · stub · {seg}\nauto: true\nRepos: app\nTickets: -\n"
        f"Segment ended without a card; Juno wrote this stub (3 tool calls).\n"
        f"Last assistant message: {excerpt}\nLearned: none\n"
    )


def real(label: str, learned: str, line: str, hhmm: str = "10:00", seg: str = S8) -> str:
    return (
        f"## 2026-10-28 {hhmm} · app · {label} · {seg}\nRepos: app\nTickets: -\n{line}\n"
        f"Learned: {learned}\n"
    )


class Settle(WriteCase):
    def setUp(self) -> None:
        super().setUp()
        self.prompt("an older session typed this", "pidold01", OLD)

    def ok(self, *argv: str) -> str:
        code, out = self.juno(*argv)
        self.assertEqual(code, 0, out)
        return out

    def refused(self, *argv: str) -> str:
        code, out = self.juno(*argv)
        self.assertEqual(code, 2, out)
        self.assertTrue(out.startswith("Juno: refused:"), out)
        return out

    def wid(self, out: str) -> str:
        return out.split("(write ")[1].split(";")[0]

    def archive(self) -> str:
        return self.get(f"archive/handoffs/{time.strftime('%Y-%m')}.md")

    def two_stubs(self) -> None:
        self.put("handoffs/app.md", "# Handoffs · app\n\n" + stub())
        other = real("x", "y", "z", seg="bbbbbbbb")
        self.put("handoffs/misc.md", "# Handoffs · misc\n\n" + stub(key="-") + "\n" + other)

    def replace_args(self, evidence: str = QUOTE, learned: str = "none") -> tuple[str, ...]:
        return (
            "card",
            "--segment",
            S8,
            "--label",
            "export fix",
            "--learned",
            learned,
            "--evidence",
            evidence,
            "Fixed the export retry.",
            "Next is the deploy.",
        )

    def test_segment_card_replaces_every_stub_in_one_undoable_write(self) -> None:
        self.two_stubs()
        out = self.ok(*self.replace_args(learned="retries need jitter"))
        for rel in ("handoffs/app.md", "handoffs/misc.md"):
            text = self.get(rel)
            self.assertNotIn("auto: true", text)
            self.assertEqual(text.count(f"· {S8}"), 1)
            self.assertIn("Learned: retries need jitter", text)
        self.assertIn("· app · export fix ·", self.get("handoffs/app.md"))
        self.assertIn("· - · export fix ·", self.get("handoffs/misc.md"))
        self.assertIn("bbbbbbbb", self.get("handoffs/misc.md"))
        self.ok("undo", self.wid(out))
        self.assertIn("auto: true", self.get("handoffs/app.md"))
        self.assertIn("auto: true", self.get("handoffs/misc.md"))

    def test_segment_card_is_refused_without_a_stub_or_with_a_real_card(self) -> None:
        self.refused(*self.replace_args())
        self.put("handoffs/app.md", "# Handoffs · app\n\n" + real("done", "a", "b"))
        self.assertIn("real card", self.refused(*self.replace_args()))
        self.put("handoffs/app.md", "# Handoffs · app\n\n" + stub())
        self.put("handoffs/misc.md", "# Handoffs · misc\n\n" + real("done", "a", "b"))
        self.refused(*self.replace_args())
        self.assertIn("auto: true", self.get("handoffs/app.md"))

    def test_segment_card_is_refused_while_a_late_run_is_pending(self) -> None:
        self.two_stubs()
        cardjob.write_job(self.cfg, OLD, {"status": "pending", "t": time.time()})
        self.assertIn("card job", self.refused(*self.replace_args()))
        self.assertIn("auto: true", self.get("handoffs/app.md"))

    def test_segment_card_needs_the_users_words_and_an_older_segment(self) -> None:
        self.two_stubs()
        self.refused(*self.replace_args(evidence=MADE_UP))
        self.put("handoffs/self.md", "# Handoffs · self\n\n" + stub(seg=self.sid[:8]))
        self.refused("card", "--segment", self.sid[:8], "--learned", "none", "--evidence", QUOTE, "a b", "c d")
        self.assertIn("auto: true", self.get("handoffs/app.md"))

    def test_dismiss_archives_every_stub_with_its_excerpt_and_undoes(self) -> None:
        self.two_stubs()
        out = self.ok("card", "dismiss", S8, "--evidence", QUOTE)
        self.assertNotIn(S8, self.get("handoffs/app.md"))
        self.assertNotIn(S8, self.get("handoffs/misc.md"))
        self.assertIn("bbbbbbbb", self.get("handoffs/misc.md"))
        archive = self.archive()
        self.assertEqual(archive.count(f"· stub · {S8}"), 2)
        self.assertEqual(archive.count("Last assistant message: Shipped the export fix."), 2)
        self.assertIn("visibility: private", archive)
        self.ok("undo", self.wid(out))
        self.assertIn("auto: true", self.get("handoffs/app.md"))
        self.assertIn("auto: true", self.get("handoffs/misc.md"))
        self.assertEqual(self.archive(), "")
        self.assertEqual(len([r for r in writelog.read(self.cfg) if r.get("link")]), 2)

    def test_dismiss_refuses_a_real_card_no_stub_bad_evidence_or_pending_run(self) -> None:
        self.refused("card", "dismiss", S8, "--evidence", QUOTE)
        self.put("handoffs/app.md", "# Handoffs · app\n\n" + real("done", "a", "b"))
        self.refused("card", "dismiss", S8, "--evidence", QUOTE)
        self.put("handoffs/app.md", "# Handoffs · app\n\n" + stub())
        self.refused("card", "dismiss", S8, "--evidence", MADE_UP)
        cardjob.write_job(self.cfg, OLD, {"status": "pending", "t": time.time()})
        self.refused("card", "dismiss", S8, "--evidence", QUOTE)
        self.assertIn("auto: true", self.get("handoffs/app.md"))

    def pair(self) -> str:
        first = real("plan", "plan the retry first", "Planned the retry.", "10:00")
        second = real("retro", "retro: jitter wins", "Retro: jitter fixed it.", "10:20")
        other = real("o", "p", "q", seg="cccccccc")
        self.put("handoffs/app.md", "# Handoffs · app\n\n" + first + "\n" + second + "\n" + other)
        return "handoffs/app.md"

    def test_merge_joins_bodies_and_learned_and_archives_the_second(self) -> None:
        rel = self.pair()
        out = self.ok("card", "merge", "app", S8, "--evidence", QUOTE)
        text = self.get(rel)
        self.assertEqual(text.count(f"· {S8}"), 1)
        self.assertIn("2026-10-28 10:00 · app · plan · " + S8, text)
        self.assertIn("Planned the retry.\nRetro: jitter fixed it.\n", text)
        self.assertEqual(text.count("Learned: plan the retry first; retro: jitter wins"), 1)
        self.assertIn("cccccccc", text)
        self.assertIn("· retro · " + S8, self.archive())
        self.assertIn("Learned: retro: jitter wins", self.archive())
        self.ok("undo", self.wid(out))
        self.assertEqual(self.get(rel).count(f"· {S8}"), 2)

    def test_merge_accepts_a_path_and_refuses_unmergeable_input(self) -> None:
        self.pair()
        self.ok("card", "merge", "handoffs/app.md", S8, "--evidence", QUOTE)
        self.refused("card", "merge", "app", S8, "--evidence", QUOTE)
        self.put("handoffs/app.md", "# Handoffs · app\n\n" + stub() + "\n" + real("x", "y", "z"))
        self.refused("card", "merge", "app", S8, "--evidence", QUOTE)
        long = real("a", "l1", "\n".join(f"line {i} of work." for i in range(9)))
        again = long.replace("l1", "l2").replace("line ", "more ")
        self.put("handoffs/app.md", "# Handoffs · app\n\n" + long + "\n" + again)
        self.assertIn("length", self.refused("card", "merge", "app", S8, "--evidence", QUOTE))
        self.assertEqual(self.get("handoffs/app.md").count(f"· {S8}"), 2)

    def test_merge_of_two_none_learned_lines_stays_none(self) -> None:
        a = real("a", "none", "Did a.")
        b = real("b", "none", "Did b.", "10:05")
        self.put("handoffs/app.md", "# Handoffs · app\n\n" + a + "\n" + b)
        self.ok("card", "merge", "app", S8, "--evidence", QUOTE)
        self.assertIn("\nLearned: none\n", self.get("handoffs/app.md"))
        self.assertNotIn("none; none", self.get("handoffs/app.md"))
