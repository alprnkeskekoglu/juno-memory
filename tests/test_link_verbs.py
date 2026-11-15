import time

from tests.test_writegate_cli import NOTE, QUOTE, SECRET, Base

TEXT = "The queue worker needs a restart after a config change"
REASON = "profile with the restart steps"
FACT = "The export job needs the staging flag set"


class LinkBase(Base):
    def setUp(self) -> None:
        super().setUp()
        self.put("projects/alpha.md", "---\nrepo: alpha\n---\n\n## Purpose\n\n- A queue service\n")
        self.put(
            "knowledge/cache.md", "---\ntopic: cache\n---\n# cache\n\n- TTL is sixty seconds\n"
        )

    def note_path(self) -> str:
        return f"notes/{time.strftime('%Y-%m-%d')}-worker-restart.md"

    def write_note(self, *extra: str) -> str:
        return self.ok("note", "--slug", "worker-restart", "--repos", "alpha", TEXT, *extra)


class NoteLink(LinkBase):
    def test_the_issue_repro_ends_with_a_links_line(self) -> None:
        self.ok("note", "--repos", "alpha", TEXT, "--link", f"alpha:{REASON}")
        (name,) = self.notes()
        lines = self.get(f"notes/{name}").splitlines()
        self.assertEqual(lines[-1], "Links: [[alpha]] (profile with the restart steps)")
        self.assertEqual(lines[-3], TEXT)

    def test_repeated_links_are_comma_separated_and_paths_work(self) -> None:
        self.write_note(
            "--link", f"alpha:{REASON}", "--link", "knowledge/cache:why the ttl matters"
        )
        last = self.get(self.note_path()).splitlines()[-1]
        self.assertEqual(
            last,
            "Links: [[alpha]] (profile with the restart steps), "
            "[[knowledge/cache]] (why the ttl matters)",
        )

    def test_a_duplicate_target_in_one_call_keeps_the_last_reason(self) -> None:
        self.write_note("--link", "alpha:the first reason", "--link", "alpha:the second reason")
        self.assertEqual(
            self.get(self.note_path()).splitlines()[-1], "Links: [[alpha]] (the second reason)"
        )

    def test_unresolved_target_is_refused_and_nothing_is_written(self) -> None:
        self.refused("note", TEXT, "--link", f"ghost:{REASON}")
        self.assertEqual(self.notes(), [])

    def test_short_reason_is_refused(self) -> None:
        for reason in ("restart", "a b c d", ""):
            self.refused("note", TEXT, "--link", f"alpha:{reason}")
        self.refused("note", TEXT, "--link", "alpha")
        self.assertEqual(self.notes(), [])

    def test_forbidden_characters_are_refused(self) -> None:
        for spec in (
            "alpha]]x:the restart steps",
            "al|pha:the restart steps",
            "[[alpha:the restart steps",
            "alpha:the restart | steps",
            "alpha:the restart ]] steps",
            "alpha:the restart · steps",
            "alpha:the restart\nsteps here",
            "alpha:the (restart) steps",
        ):
            with self.subTest(spec):
                self.refused("note", TEXT, "--link", spec)
        self.assertEqual(self.notes(), [])

    def test_the_reason_is_privacy_scanned(self) -> None:
        self.refused("note", TEXT, "--link", f"alpha:token is {SECRET} here")
        self.assertEqual(self.notes(), [])

    def test_without_link_nothing_changes(self) -> None:
        self.write_note()
        self.assertNotIn("Links:", self.get(self.note_path()))


class KnowledgeLink(LinkBase):
    def add(self, *extra: str, fact: str = FACT) -> str:
        return self.ok("knowledge", "add", "--topic", "export", "--evidence", QUOTE, fact, *extra)

    def test_links_line_is_last_and_extends_across_adds(self) -> None:
        self.add("--link", "alpha:the service that runs it")
        lines = self.get("knowledge/export.md").splitlines()
        self.assertEqual(lines[-1], "Links: [[alpha]] (the service that runs it)")
        self.assertIn("- " + FACT, lines[-3])
        self.add(fact="The export job writes one file per day")
        lines = self.get("knowledge/export.md").splitlines()
        self.assertEqual(lines[-1], "Links: [[alpha]] (the service that runs it)")
        self.assertTrue(lines[-3].startswith("- The export job writes one file"))
        self.add(
            "--link", "knowledge/cache:cache facts", "--link", "alpha:the profile of that service",
            fact="The export job reads the cache first",
        )  # fmt: skip
        lines = self.get("knowledge/export.md").splitlines()
        self.assertEqual(
            lines[-1],
            "Links: [[alpha]] (the profile of that service), [[knowledge/cache]] (cache facts)",
        )
        self.assertEqual(sum(x.startswith("Links:") for x in lines), 1)
        self.assertEqual(sum(x.startswith("- ") for x in lines), 3)

    def test_an_existing_trailing_links_line_stays_last_without_link(self) -> None:
        self.put(
            "knowledge/export.md",
            "---\ntopic: export\n---\n# export\n\n- An older export fact line\n\n"
            "Links: [[alpha]] (the service that runs it)\n",
        )
        self.add()
        lines = self.get("knowledge/export.md").splitlines()
        self.assertEqual(lines[-1], "Links: [[alpha]] (the service that runs it)")
        self.assertEqual(sum(x.startswith("- ") for x in lines), 2)

    def test_bad_links_are_refused(self) -> None:
        self.refused(
            "knowledge", "add", "--topic", "export", "--evidence", QUOTE, FACT,
            "--link", "ghost:the missing target",
        )  # fmt: skip
        self.refused(
            "knowledge", "add", "--topic", "export", "--evidence", QUOTE, FACT,
            "--link", "alpha:short",
        )  # fmt: skip
        self.assertEqual(self.get("knowledge/export.md"), "")


class LinkVerb(LinkBase):
    def seed(self) -> str:
        self.write_note()
        return self.note_path()

    def link(self, rel: str, *specs: str) -> str:
        argv = ["link", rel, "--evidence", QUOTE]
        for spec in specs:
            argv += ["--link", spec]
        return self.ok(*argv)

    def test_adds_a_links_line_as_one_undoable_write(self) -> None:
        rel = self.seed()
        before = self.get(rel)
        out = self.link(rel, f"alpha:{REASON}", "knowledge/cache:why the ttl matters")
        after = self.get(rel)
        self.assertTrue(after.startswith(before.rstrip("\n")))
        self.assertEqual(after.count("Links:"), 1)
        self.assertEqual(
            after.splitlines()[-1],
            "Links: [[alpha]] (profile with the restart steps), "
            "[[knowledge/cache]] (why the ttl matters)",
        )
        self.assertEqual(out.count("write w-"), 1)
        self.ok("undo", self.wid(out))
        self.assertEqual(self.get(rel), before)

    def test_extends_a_links_line_and_updates_a_duplicate_without_touching_other_lines(
        self,
    ) -> None:
        self.put(
            "knowledge/export.md",
            "---\ntopic: export\n---\n# export\n\n- A fact line that stays\n\n"
            "Links: [[alpha]] (the old reason here)\n",
        )
        before = self.get("knowledge/export.md")
        out = self.link("knowledge/export.md", "alpha:the new reason here", "cache:cache facts")
        lines = self.get("knowledge/export.md").splitlines()
        self.assertEqual(lines[:-1], before.splitlines()[:-1])
        self.assertEqual(
            lines[-1], "Links: [[alpha]] (the new reason here), [[cache]] (cache facts)"
        )
        self.ok("undo", self.wid(out))
        self.assertEqual(self.get("knowledge/export.md"), before)

    def test_a_links_line_that_is_not_ours_is_refused(self) -> None:
        self.put(
            "notes/2026-11-01-odd.md",
            "---\ndate: 2026-11-01\n---\n\n" + NOTE + "\n\nLinks: see the vault\n",
        )
        self.refused(
            "link", "notes/2026-11-01-odd.md", "--evidence", QUOTE, "--link", f"alpha:{REASON}"
        )

    def test_refusals(self) -> None:
        rel = self.seed()
        spec = f"alpha:{REASON}"
        self.refused("link", "projects/alpha.md", "--evidence", QUOTE, "--link", spec)
        self.refused("link", "inbox.md", "--evidence", QUOTE, "--link", spec)
        self.refused("link", "notes/none.md", "--evidence", QUOTE, "--link", spec)
        self.refused("link", "../x.md", "--evidence", QUOTE, "--link", spec)
        self.refused("link", rel, "--evidence", QUOTE, "--link", f"ghost:{REASON}")
        self.refused("link", rel, "--evidence", QUOTE, "--link", "alpha:short")
        self.refused(
            "link", rel, "--evidence", "words nobody typed in this segment", "--link", spec
        )
        self.refused("link", rel, "--evidence", QUOTE)
        self.assertNotIn("Links:", self.get(rel))
