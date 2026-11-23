import os

from juno.adapters import writelog
from juno.core import writegate
from tests import test_card_settle as cs
from tests import test_writegate_cli as wg

MADE_UP, QUOTE, S8, stub = cs.MADE_UP, cs.QUOTE, cs.S8, cs.stub
SECRET = wg.SECRET

LINES = ("Fixed the export timeout.", "Stopped before the deploy.")
BASE = ("card", "--label", "export fix", "--learned", "none")


def tree(*roots: str) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for root in roots:
        for folder, _, names in os.walk(root):
            for name in names:
                path = os.path.join(folder, name)
                with open(path, "rb") as handle:
                    out[path] = handle.read()
    return out


class DryRun(wg.Base):
    def snap(self) -> dict[str, bytes]:
        return tree(self.sb.vault, self.sb.state)

    def test_prints_card_scan_and_target_and_writes_nothing(self) -> None:
        self.touch_repo("app")
        before = self.snap()
        out = self.ok(*BASE, "--dry-run", *LINES)
        self.assertEqual(self.snap(), before)
        self.assertIn("dry run, nothing written", out)
        self.assertIn("Target: handoffs/app.md (create the file and add the card)", out)
        self.assertIn("Privacy scan: clean", out)
        self.assertRegex(out, rf"## \d{{4}}-\d\d-\d\d \d\d:\d\d · - · export fix · {self.sid[:8]}")
        self.assertIn("Fixed the export timeout.", out)
        self.assertIn("Learned: none", out)
        self.assertEqual(writelog.read(self.cfg), [])

    def test_says_replace_or_append(self) -> None:
        self.touch_repo("app")
        self.put("handoffs/app.md", "# Handoffs · app\n\n" + stub("bbbbbbbb"))
        self.assertIn("(append the card)", self.ok(*BASE, "--dry-run", *LINES))
        self.ok(*BASE, *LINES)
        before = self.snap()
        out = self.ok(*BASE, "--dry-run", "Fixed it differently.", "Next is more.")
        self.assertIn("(replace this segment's card)", out)
        self.assertEqual(self.snap(), before)
        self.assertIn("no change", self.ok(*BASE, "--dry-run", *LINES).replace("\n", " ") or "")

    def test_shows_the_link_repaired_card(self) -> None:
        self.touch_repo("app")
        out = self.ok(*BASE, "--dry-run", "Fixed it, see [[app]].", "Next is more.")
        self.assertIn("Fixed it, see app.", out)
        self.assertNotIn("[[app]]", out)

    def test_not_counted_against_the_write_cap(self) -> None:
        self.touch_repo("app")
        for _ in range(3):
            self.ok(*BASE, "--dry-run", *LINES)
        self.assertEqual(writelog.live_count(writelog.read(self.cfg), self.sid), 0)

    def test_real_run_cap_refusal_is_shared(self) -> None:
        self.touch_repo("app")
        for n in range(writegate.RUN_MAX):
            writelog.append(self.cfg, {"id": f"w-{n:08x}", "sid": self.sid, "verb": "note"})
        real = self.refused(*BASE, *LINES)
        before = self.snap()
        self.assertEqual(self.refused(*BASE, "--dry-run", *LINES), real)
        self.assertEqual(self.snap(), before)

    def assert_same_refusal(self, args: tuple[str, ...], env: dict[str, str] | None = None) -> None:
        self.touch_repo("app")
        real = self.refused(*args, env=env)
        before = self.snap()
        dry = self.refused(*args, "--dry-run", env=env)
        self.assertEqual(dry, real)
        self.assertEqual(self.snap(), before)

    def test_refusals_match_a_real_run(self) -> None:
        cases = {
            "one line": ("card", "--learned", "none", "Only one line"),
            "secret": ("card", "--learned", SECRET + " token", *LINES),
            "invalid": ("card", "--learned", "none", "You fixed the export.", "Next is more."),
            "no learned": ("card", *LINES),
            "bare link": (*BASE, "--links", "[[app]]", *LINES),
        }
        for name, args in cases.items():
            with self.subTest(name):
                self.assert_same_refusal(args)

    def test_subagent_and_missing_ledger_refusals(self) -> None:
        self.assert_same_refusal((*BASE, *LINES), {"CLAUDE_CODE_AGENT_ID": "a1"})
        self.assert_same_refusal((*BASE, *LINES), {"CLAUDE_CODE_SESSION_ID": "no-such-ledger"})

    def test_nosave_is_refused_like_a_real_run(self) -> None:
        self.prompt("keep this one out [nosave]", "pid00002", nosave=True)
        self.assert_same_refusal((*BASE, *LINES))

    def test_does_not_wait_for_the_vault_lock(self) -> None:
        from juno.adapters import fs

        self.touch_repo("app")
        with fs.locked(f"{self.sb.state}/vault.lock", 1.0):
            self.assertIn("dry run", self.ok(*BASE, "--dry-run", *LINES))
            self.refused(*BASE, *LINES)
        self.assertEqual(self.get("handoffs/app.md"), "")

    def test_dismiss_and_merge_are_not_previewed(self) -> None:
        out = self.refused("card", "dismiss", S8, "--evidence", QUOTE, "--dry-run")
        self.assertIn("previews a card only", out)
        out = self.refused("card", "merge", "app", S8, "--evidence", QUOTE, "--dry-run")
        self.assertIn("previews a card only", out)


class DryRunSegment(cs.Settle):
    def args(self, *extra: str, evidence: str = QUOTE) -> tuple[str, ...]:
        return (*self.replace_args(evidence), *extra)

    def snap(self) -> dict[str, bytes]:
        return tree(self.sb.vault, self.sb.state)

    def test_previews_the_replacement_and_writes_nothing(self) -> None:
        self.two_stubs()
        before = self.snap()
        out = self.ok(*self.args("--dry-run"))
        self.assertEqual(self.snap(), before)
        self.assertIn("Target: handoffs/app.md (replace this segment's card)", out)
        self.assertIn("Target: handoffs/misc.md (replace this segment's card)", out)
        self.assertIn("Fixed the export retry.", out)
        self.assertEqual(writelog.read(self.cfg), [])
        real = self.ok(*self.replace_args())
        self.assertIn("write w-", real)
        self.assertNotIn("auto: true", self.get("handoffs/app.md"))

    def test_refusals_match_a_real_run(self) -> None:
        self.two_stubs()
        for name, args in {
            "invented evidence": self.args(evidence=MADE_UP),
            "own segment": (
                "card",
                "--segment",
                self.sid[:8],
                "--evidence",
                QUOTE,
                "--learned",
                "none",
                *self.replace_args()[7:],
            ),
        }.items():
            with self.subTest(name):
                real = self.refused(*args)
                before = self.snap()
                self.assertEqual(self.refused(*args, "--dry-run"), real)
                self.assertEqual(self.snap(), before)

    def test_unknown_segment_refuses_the_same(self) -> None:
        args = list(self.replace_args())
        args[2] = "cccccccc"
        real = self.refused(*args)
        self.assertEqual(self.refused(*args, "--dry-run"), real)
