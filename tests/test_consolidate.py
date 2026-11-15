import os
import random
import unittest

from juno.adapters import writelog
from juno.cmd import cap
from juno.core import writegate
from tests.support.stalefix import World
from tests.support.write import WriteCase

QUOTE = "this is the first typed prompt"
REL = "projects/demo.md"
SAME = {
    3: "- The invoice export job times out above ten thousand rows · 2026-01-02",
    11: "- Exporting more than ten thousand rows makes the invoice export job time out",
    27: "- Invoice export times out when the rows exceed ten thousand",
}
NEW = "- Invoice export times out above ten thousand rows"


def other_line(rng: random.Random) -> str:
    syl = ["ka", "to", "mi", "re", "su", "lo", "vi", "pa", "ne", "du", "xo", "ba", "gi", "ze"]
    words = ["".join(rng.choice(syl) for _ in range(rng.randint(3, 5))) for _ in range(9)]
    return "- " + " ".join(words) + " is set by the build"


def demo_text() -> str:
    rng = random.Random(7)
    lines = ["# demo", "## Gotchas"]
    for number in range(3, 43):
        lines.append(SAME.get(number) or other_line(rng))
    lines += ["", "## How to run", "- Run `make test` before every push to the branch"]
    return "\n".join(lines) + "\n"


class Base(WriteCase):
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

    def used(self) -> int:
        return cap.used(self.cfg, self.sid)


class Consolidate(Base):
    def test_lists_the_group_with_line_numbers_and_writes_nothing(self) -> None:
        self.put(REL, demo_text())
        before = self.get(REL)
        out = self.ok("consolidate", REL, "--section", "Gotchas")
        self.assertIn("lines 3, 11, 27", out)
        self.assertEqual(out.count("group "), 1, out)
        for number, text in SAME.items():
            self.assertIn(f"{number}: {text}", out)
        self.assertEqual(self.get(REL), before)
        self.assertEqual(writelog.read(self.cfg), [])

    def test_unknown_section_and_missing_file_are_refused(self) -> None:
        self.put(REL, demo_text())
        self.refused("consolidate", REL, "--section", "Nope")
        self.refused("consolidate", "projects/none.md", "--section", "Gotchas")

    def test_section_bounds_the_scan(self) -> None:
        self.put(REL, demo_text())
        out = self.ok("consolidate", REL, "--section", "How to run")
        self.assertIn("no near-duplicate", out)


class ConsolidateGone(Base):
    def test_lists_lines_citing_a_file_gone_from_the_default_branch(self) -> None:
        world = World(self.sb)
        world.config()
        world.git(world.app, "fetch", "-q", "origin")
        gone = "- The legacy exporter lives in `src/old.py` and runs nightly"
        kept = "- Backoff is 2 seconds, set in `src/retry.py` for every call"
        self.put("projects/app.md", "# app\n## Gotchas\n" + gone + "\n" + kept + "\n")
        out = self.ok("consolidate", "projects/app.md", "--section", "Gotchas")
        self.assertIn("src/old.py", out)
        self.assertIn("3: " + gone, out)
        self.assertNotIn("4: " + kept, out)


class Merge(Base):
    def merge(self, nums: str = "3,11,27") -> list[str]:
        return [
            "fix", "--merge", f"{REL}:{nums}",
            "--was", "invoice export job times out",
            "--was", "ten thousand rows makes",
            "--was", "times out when the rows",
            "--evidence", QUOTE, NEW,
        ]  # fmt: skip

    def test_one_write_one_cap_unit_archive_and_byte_exact_undo(self) -> None:
        self.put(REL, demo_text())
        before = self.get(REL)
        used = self.used()
        out = self.ok(*self.merge())
        after = self.get(REL).splitlines()
        self.assertEqual(len(after), len(before.splitlines()) - 2)
        self.assertEqual(after[2], NEW)
        self.assertEqual(self.used(), used + 1)
        main = [r for r in writelog.read(self.cfg) if not r.get("link")]
        self.assertEqual([r["id"] for r in main], [self.wid(out)])
        archive = [n for n in os.listdir(self.vp("archive/decayed")) if n.startswith("forgotten-")]
        kept = self.get("archive/decayed/" + archive[0])
        for text in SAME.values():
            self.assertIn(text, kept)
        self.ok("undo", self.wid(out))
        self.assertEqual(self.get(REL), before)
        self.assertFalse(os.path.exists(self.vp("archive/decayed/" + archive[0])))
        self.assertEqual(self.used(), used)

    def test_works_at_the_cap_edge(self) -> None:
        self.put(REL, demo_text())
        for n in range(writegate.RUN_MAX - 1):
            writelog.append(self.cfg, {"id": f"w-{n:08x}", "sid": self.sid, "verb": "note"})
        self.ok(*self.merge())
        self.assertEqual(self.used(), writegate.RUN_MAX)

    def test_each_was_must_match_its_line(self) -> None:
        self.put(REL, demo_text())
        before = self.get(REL)
        argv = self.merge()
        argv[argv.index("ten thousand rows makes")] = "not in that line at all"
        self.refused(*argv)
        self.assertEqual(self.get(REL), before)
        argv = self.merge()
        at = argv.index("--was")
        del argv[at : at + 2]  # one --was too few
        self.refused(*argv)
        self.assertEqual(self.get(REL), before)

    def test_refuses_across_sections_and_bad_refs(self) -> None:
        self.put(REL, demo_text())
        before = self.get(REL)
        self.refused(*self.merge("3,11,45"))
        self.refused(*self.merge("3,3,11"))
        self.refused(*self.merge("3"))
        self.refused(*self.merge("3,11,99"))
        self.refused(*self.merge("1,3,11"))
        self.assertEqual(self.get(REL), before)

    def test_evidence_must_be_anchored(self) -> None:
        self.put(REL, demo_text())
        argv = self.merge()
        argv[argv.index(QUOTE)] = "words nobody typed in this session"
        self.refused(*argv)

    def test_plain_fix_still_works(self) -> None:
        self.put(REL, demo_text())
        self.ok(
            "fix", f"{REL}:3", "--was", "invoice export job", "--evidence", QUOTE,
            "The invoice export job times out above eleven thousand rows",
        )  # fmt: skip
        self.assertIn("eleven thousand", self.get(REL))


if __name__ == "__main__":
    unittest.main()
