import json
import os
import time
from typing import Any, ClassVar

from juno.adapters import queues
from juno.core import candidates, extract
from tests.support.cardcase import CardCase, card, with_extract
from tests.support.hooks import SID

QUOTE = "always run make lint before you commit"
FACT = {
    "file": "projects/billing-api.md",
    "text": "Money rounding lives in src/money.py",
    "evidence": "src/money.py",
}


class ExtractCase(CardCase):
    def ext(self, text: str | None = None, **kinds: Any) -> dict[str, Any]:
        return self.run_card(with_extract(text or card(), kinds))

    def start(self, prompt: str = QUOTE) -> None:
        self.dirty(prompt + ", and fix src/money.py")


class RuleCandidates(ExtractCase):
    def rule(self, **extra: object) -> dict[str, object]:
        return {
            "rule": "Run make lint before committing",
            "scope": "global",
            "quote": QUOTE,
            **extra,
        }

    def pending(self) -> list[dict[str, Any]]:
        return queues.pending(self.cfg(), queues.RULES)

    def test_verified_candidate_is_queued_never_written(self) -> None:
        self.start()
        row = self.ext(rules=[self.rule()])
        self.assertEqual(row["rules"]["queued"], 1)
        self.assertEqual(self.sections(row)["rules"], "ok")
        (item,) = self.pending()
        self.assertRegex(item["id"], r"^r[0-9a-f]{6}$")
        self.assertEqual(
            (item["scope"], item["quote"], item["status"], item["seg8"]),
            ("global", QUOTE, "pending", SID[:8]),
        )
        self.assertEqual(item["pid"], "89dd5955")
        self.assertFalse(
            os.path.exists(self.vp("rules/global.md"))
            and "make lint" in self.read("rules/global.md")
        )
        self.assertEqual(
            oct(os.stat(queues.path(self.cfg(), queues.RULES)).st_mode & 0o777), "0o600"
        )

    def test_turkish_quote_is_folded_under_the_tr_preset(self) -> None:
        self.set_config('[language]\npreset = "tr"\n')
        self.start("Bundan sonra HER ZAMAN turkce yaz")
        row = self.ext(
            rules=[self.rule(rule="Write Turkish", quote="bundan sonra her zaman türkçe yaz")]
        )
        self.assertEqual(row["rules"]["queued"], 1)

    def test_repo_named_like_a_topic_file_is_allowed(self) -> None:
        ctx = extract.RuleContext(sources=[], repos=["topic-tool"])
        self.assertEqual(extract.check_scope(ctx, "repo:topic-tool", []), ("repo:topic-tool", []))
        self.assertIsNone(extract.check_scope(ctx, "repo:global", []))

    def test_unverified_invalid_and_secret_candidates_are_dropped(self) -> None:
        self.start()
        bad = [
            self.rule(quote="the assistant said to always run make lint"),
            self.rule(scope="repo:unknown-repo"),
            self.rule(scope="ticket:ABC-1"),
            self.rule(scope="topic:Bad Slug"),
            self.rule(rule="short"),
            self.rule(rule="x" * 400),
            self.rule(quote="too short"),
            self.rule(rule="Set password=abc123def456ghi789 always"),
        ]
        row = self.ext(rules=bad)
        self.assertEqual(row["rules"]["queued"], 0)
        self.assertEqual(row["rules"]["unverified"], 1)
        self.assertEqual(self.sections(row)["rules"], "dropped:invalid")
        self.assertEqual(self.pending(), [])

    def test_topic_scope_needs_triggers_unless_the_topic_exists(self) -> None:
        self.start(QUOTE + " and also use pytest quietly please")
        row = self.ext(rules=[self.rule(scope="topic:linting")])
        self.assertEqual(row["rules"]["invalid"], 1)
        self.redirty()
        row = self.ext(rules=[self.rule(scope="topic:linting", triggers=["make lint", "ruff"])])
        self.assertEqual(self.pending()[0]["triggers"], ["make lint", "ruff"])
        self.assertEqual(row["rules"]["queued"], 1)
        self.write("rules/topics/testing.md", "triggers: pytest\n\n")
        self.redirty()
        self.ext(
            rules=[
                self.rule(
                    rule="Run pytest quietly",
                    scope="topic:testing",
                    quote="use pytest quietly please",
                )
            ]
        )
        self.assertEqual(self.pending()[1]["triggers"], [])

    def test_duplicates_of_rule_files_and_the_queue_are_dropped(self) -> None:
        self.start()
        self.write(
            "rules/global.md",
            '- 2026-01-01 · Run make lint before committing · src: user abcd1234 · "x y z"\n',
        )
        row = self.ext(rules=[self.rule()])
        self.assertEqual(row["rules"]["duplicate"], 1)
        self.write("rules/global.md", "")
        self.redirty()
        self.ext(rules=[self.rule()])
        self.redirty()
        row = self.ext(rules=[self.rule(rule="Lint first, always")])
        self.assertEqual(row["rules"]["duplicate"], 1)
        self.assertEqual(len(self.pending()), 1)

    def test_caps_per_run_and_pending(self) -> None:
        prompt = " ".join(f"always do thing number {i} before the commit." for i in range(8))
        self.start(prompt)
        many = [
            self.rule(
                rule=f"Do thing number {i} before the commit",
                quote=f"always do thing number {i} before the commit.",
            )
            for i in range(8)
        ]
        row = self.ext(rules=many)
        self.assertEqual(row["rules"]["queued"], 3)
        self.assertEqual(row["rules"]["capped"], 5)
        cfg = self.cfg()
        queues.update(
            cfg,
            queues.RULES,
            lambda q: q.extend(
                {
                    "id": candidates.rule_id("x", str(i)),
                    "rule": f"r{i}",
                    "quote": f"q{i}",
                    "t": time.time(),
                    "status": "pending",
                }
                for i in range(12)
            ),
        )
        self.redirty()
        row = self.ext(
            rules=[
                self.rule(
                    rule="Another one entirely", quote="always do thing number 7 before the commit."
                )
            ]
        )
        self.assertEqual(row["rules"]["capped"], 1)

    def test_corrupt_or_huge_queue_reads_empty(self) -> None:
        cfg = self.cfg()
        os.makedirs(os.path.dirname(queues.path(cfg, queues.RULES)), exist_ok=True)
        with open(queues.path(cfg, queues.RULES), "w", encoding="utf-8") as handle:
            handle.write("{broken")
        self.assertEqual(queues.read(cfg, queues.RULES), [])
        with open(queues.path(cfg, queues.RULES), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"v": 1, "items": [{"id": "bogus"}, {"id": "r000000"}]}))
        self.assertEqual([i["id"] for i in queues.read(cfg, queues.RULES)], ["r000000"])


class KnowledgeWrites(ExtractCase):
    def test_gotcha_written_with_backup_into_a_new_profile(self) -> None:
        self.start()
        row = self.ext(knowledge=[FACT])
        self.assertEqual(self.sections(row)["knowledge"], "ok")
        self.assertEqual(row["profiles_created"], 1)
        text = self.read("projects/billing-api.md")
        self.assertIn("repo: billing-api\n", text)
        self.assertIn(
            f"- Money rounding lives in src/money.py (src/money.py; segment {SID[:8]}).", text
        )
        self.assertIn("## Gotchas\n- Money", text)
        self.assertIn("## Purpose\nNot described yet.", text)

    def test_existing_profile_is_backed_up_and_capped(self) -> None:
        body = "---\nrepo: billing-api\nupdated: 2020-01-01\n---\n## Gotchas\n"
        self.write("projects/billing-api.md", body + "".join(f"- g{i}\n" for i in range(25)))
        self.start()
        row = self.ext(knowledge=[FACT])
        self.assertEqual(self.sections(row)["knowledge"], "dropped:full")
        self.write(
            "projects/billing-api.md",
            "---\nrepo: billing-api\nupdated: 2020-01-01\n---\n## Purpose\nx\n",
        )
        self.redirty()
        row = self.ext(knowledge=[FACT])
        text = self.read("projects/billing-api.md")
        self.assertIn("\n## Gotchas\n- Money rounding", text)
        self.assertNotIn("updated: 2020-01-01", text)
        backups = os.path.join(self.sb.state, "backups")
        found = [
            n
            for _, _, names in os.walk(backups)
            for n in names
            if n.endswith("projects%billing-api.md")
        ]
        self.assertTrue(found)

    def test_new_knowledge_file_needs_a_title_and_appends_later(self) -> None:
        self.start()
        item = {
            "file": "knowledge/money-rounding.md",
            "text": "Rounding is banker's rounding here",
            "evidence": "src/money.py",
        }
        row = self.ext(knowledge=[item])
        self.assertEqual(row["knowledge_dropped"], {"no title": 1})
        item = dict(item, title="Money rounding", repos=["billing-api"], triggers=["rounding"])
        self.redirty()
        self.ext(knowledge=[item])
        text = self.read("knowledge/money-rounding.md")
        self.assertIn("repos: [billing-api]", text)
        self.assertIn("triggers: [rounding]", text)
        self.assertIn("sources: [", text)
        self.assertIn("# Money rounding", text)
        self.redirty()
        self.ext(knowledge=[dict(item, text="Totals round once at the end of the order")])
        self.assertIn("Totals round once", self.read("knowledge/money-rounding.md"))

    def test_refusals(self) -> None:
        self.start()
        self.write("knowledge/hidden.md", "---\nvisibility: private\n---\nx\n")
        self.write("knowledge/old.md", "---\nstatus: archived\n---\nx\n")
        cases = {
            "other repo profile": dict(FACT, file="projects/other-repo.md"),
            "bad path": dict(FACT, file="rules/global.md"),
            "private file": dict(FACT, file="knowledge/hidden.md"),
            "archived file": dict(FACT, file="knowledge/old.md"),
        }
        for name, item in cases.items():
            with self.subTest(name):
                row = self.ext(knowledge=[item])
                self.assertTrue(self.sections(row)["knowledge"].startswith("dropped:"), name)
                self.redirty()
        self.assertFalse(os.path.exists(self.vp("projects/other-repo.md")))

    def test_privacy_evidence_voice_and_anchoring(self) -> None:
        self.start()
        cases = {
            "private": dict(FACT, text="Contact ops at ops@example.com about src/money.py"),
            "no evidence": dict(FACT, evidence=""),
            "unanchored": dict(FACT, evidence="src/never-seen.py"),
            "you": dict(FACT, text="You should look at src/money.py for rounding"),
            "short": dict(FACT, text="too short"),
            "id": dict(FACT, text="Partner 12345678 has odd rounding in src/money.py"),
        }
        row = self.ext(knowledge=list(cases.values()))
        self.assertEqual(
            row["knowledge_dropped"],
            {"private": 2, "no evidence": 1, "unanchored": 1, "invalid": 2},
        )
        self.assertEqual(self.sections(row)["knowledge"], "dropped:private")

    def test_caps_and_dedupe(self) -> None:
        self.start()
        facts = [
            dict(FACT, text=f"Rounding fact number {i} lives in src/money.py") for i in range(6)
        ]
        row = self.ext(knowledge=facts)
        self.assertEqual(len(row["knowledge"]), 2)
        self.assertEqual(row["knowledge_dropped"], {"capped": 4})
        self.redirty()
        row = self.ext(knowledge=[facts[0]])
        self.assertEqual(row["knowledge_dropped"], {"duplicate": 1})

    def test_profile_is_not_created_for_a_plain_folder_or_a_symlink(self) -> None:
        os.makedirs(os.path.join(self.projects, "plain-folder"))
        self.start()
        row = self.ext(knowledge=[dict(FACT, file="projects/plain-folder.md")])
        self.assertEqual(row["knowledge_dropped"], {"path": 1})
        self.assertFalse(os.path.exists(self.vp("projects/plain-folder.md")))
        outside = os.path.join(self.sb.root, "outside.md")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("## Gotchas\n")
        os.symlink(outside, self.vp("projects/billing-api.md"))
        self.redirty()
        row = self.ext(knowledge=[FACT])
        self.assertEqual(row["knowledge_dropped"], {"path": 1})
        with open(outside, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "## Gotchas\n")

    def test_prompt_lists_repos_without_a_profile(self) -> None:
        self.start()
        self.ext()
        prompt = self.calls[0]["prompt"]
        self.assertIn("Segment repos without a profile", prompt)
        self.assertIn("billing-api (none yet, created on first use)", prompt)
        self.assertIn("Vault state (for dedupe):", prompt)
        self.assertIn("---JUNO-EXTRACT---", prompt)


class Threads(ExtractCase):
    def test_close_and_open(self) -> None:
        line = "- [billing-api] Decide on rounding mode · since 2026-01-01"
        self.write("threads.md", f"# Threads\n\n{line}\n- [other] unrelated · since 2026-01-01\n")
        self.start()
        threads = {
            "close": [line, "- [other] unrelated · since 2026-01-01", "- [billing-api] not there"],
            "open": [{"tag": "billing-api", "text": "Ask finance about half-cent refunds"}],
        }
        row = self.ext(threads=threads)
        text = self.read("threads.md")
        self.assertNotIn("Decide on rounding mode", text)
        self.assertIn("- [other] unrelated", text)
        self.assertRegex(
            text, r"- \[billing-api\] Ask finance about half-cent refunds · since \d{4}-"
        )
        self.assertEqual(row["threads"]["dropped"], 2)
        self.assertEqual(self.sections(row)["threads"], "ok")

    def test_open_refusals(self) -> None:
        self.start()
        opens = [
            {"tag": "unknown", "text": "Ask finance about half-cent refunds"},
            {"tag": "billing-api", "text": "short"},
            {"tag": "billing-api", "text": "Contains · a middle dot in the text"},
            {"tag": "billing-api", "text": "Mail ops@example.com about the refunds"},
        ]
        for item in opens:
            self.redirty()
            row = self.ext(threads={"open": [item]})
            self.assertEqual(self.sections(row)["threads"], "dropped:invalid")
        self.assertNotIn("Ask finance", self.read("threads.md"))

    def test_symlinked_threads_file_is_refused(self) -> None:
        target = os.path.join(self.sb.root, "real-threads.md")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("# Threads\n")
        os.unlink(self.vp("threads.md"))
        os.symlink(target, self.vp("threads.md"))
        self.start()
        row = self.ext(
            threads={
                "open": [{"tag": "billing-api", "text": "Ask finance about half-cent refunds"}]
            }
        )
        self.assertEqual(self.sections(row)["threads"], "dropped:path")
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "# Threads\n")


class Pitfalls(ExtractCase):
    ITEM: ClassVar[dict[str, Any]] = {
        "text": "Check the branch with git status before editing src/money.py",
        "evidence": "src/money.py",
        "triggers": ["money.py"],
    }

    def test_writes_a_valid_pitfall_line(self) -> None:
        self.start()
        row = self.ext(pitfalls=[self.ITEM])
        self.assertEqual(self.sections(row)["pitfalls"], "ok")
        text = self.read("pitfalls.md")
        self.assertRegex(
            text,
            rf"- Check the branch .* · triggers: money.py · src/money.py; segment {SID[:8]} · \d{{4}}-\d\d-\d\d",
        )
        from juno.core import topics

        self.assertEqual(len(topics.pitfall_lines(text)), 1)

    def test_rejections(self) -> None:
        self.start()
        cases = [
            dict(self.ITEM, text="short"),
            dict(self.ITEM, text="Contains · dot and enough characters to pass"),
            dict(self.ITEM, text="Link to [[something]] and enough characters here"),
            dict(self.ITEM, evidence="src/nope.py"),
            dict(self.ITEM, triggers="money"),
            dict(self.ITEM, triggers=["a", "b", "c", "d", "e", "f"]),
            dict(self.ITEM, triggers=["x"]),
            dict(self.ITEM, text="Password is password=abc123def456ghi789 always"),
        ]
        row = self.ext(pitfalls=cases[:4])
        self.assertEqual(row["pitfalls"]["written"], [])
        self.assertEqual(row["pitfalls"]["dropped"], {"invalid": 3, "unanchored": 1})
        self.redirty()
        row = self.ext(pitfalls=cases[4:])
        self.assertEqual(row["pitfalls"]["dropped"], {"trigger": 3, "private": 1})
        self.redirty()
        row = self.ext(pitfalls=cases)
        self.assertEqual(row["pitfalls"]["dropped"]["capped"], 4)

    def test_duplicate_and_per_run_cap_and_no_file_cap(self) -> None:
        self.start()
        items = [
            dict(self.ITEM, text=f"Pitfall number {i} about src/money.py usage") for i in range(3)
        ]
        row = self.ext(pitfalls=[*items, items[0]])
        self.assertEqual(len(row["pitfalls"]["written"]), 2)
        self.assertEqual(row["pitfalls"]["dropped"], {"capped": 1, "duplicate": 1})
        lines = "".join(f"- p{i} · triggers: - · e; segment x · 2026-01-01\n" for i in range(60))
        self.write("pitfalls.md", "# Pitfalls\n\n" + lines)
        self.redirty()
        row = self.ext(
            pitfalls=[
                dict(self.ITEM, text="A pitfall that arrives past sixty lines in src/money.py")
            ]
        )
        self.assertEqual(len(row["pitfalls"]["written"]), 1)
        self.assertEqual(row["pitfalls"]["dropped"], {})

    def test_symlinked_pitfalls_file_is_refused(self) -> None:
        target = os.path.join(self.sb.root, "real-pitfalls.md")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("# Pitfalls\n")
        os.unlink(self.vp("pitfalls.md"))
        os.symlink(target, self.vp("pitfalls.md"))
        self.start()
        row = self.ext(pitfalls=[self.ITEM])
        self.assertEqual(self.sections(row)["pitfalls"], "dropped:path")


class BacklogSuggestions(ExtractCase):
    def item(
        self, slug: str = "fix-rounding", status: str = "doing", tickets: str = "[ABC-111111]"
    ) -> None:
        self.write(
            f"backlog/{slug}.md",
            f"---\ntitle: Fix rounding\nstatus: {status}\npriority: p2\nadded: 2026-11-01\nupdated: 2026-11-01\ntickets: {tickets}\nrepos: [billing-api]\n---\n## Task\nx\n",
        )

    def test_suggestion_is_queued_and_prompted(self) -> None:
        self.item()
        self.start()
        entry = {
            "slug": "fix-rounding",
            "log": "Rounding fixed in src/money.py and tests pass",
            "status": "waiting",
        }
        row = self.ext(backlog=[entry, {"slug": "unknown", "log": "Something about src/money.py"}])
        self.assertIn("Open backlog items mentioned in this segment", self.calls[0]["prompt"])
        self.assertIn("fix-rounding · Fix rounding · doing · ABC-111111", self.calls[0]["prompt"])
        self.assertEqual(row["backlog"], {"proposed": 2, "queued": 1, "dropped": 1})
        self.assertEqual(self.sections(row)["backlog"], "ok")
        (queued,) = queues.pending(self.cfg(), queues.BACKLOG)
        self.assertRegex(queued["id"], r"^b[0-9a-f]{6}$")
        self.assertEqual((queued["slug"], queued["new_status"]), ("fix-rounding", "waiting"))

    def test_done_ticket_adds_an_engine_suggestion(self) -> None:
        self.item()
        self.write("tasks/ABC-111111.md", "---\nstatus: done\n---\n")
        self.start()
        row = self.ext(backlog=[{"slug": "fix-rounding", "log": "Looked at src/money.py again"}])
        queued = {i["new_status"] for i in queues.pending(self.cfg(), queues.BACKLOG)}
        self.assertEqual(queued, {None, "done"})
        self.assertEqual(row["backlog"]["queued"], 2)

    def test_no_backlog_dir_means_no_backlog_prompt(self) -> None:
        self.start()
        self.ext()
        self.assertNotIn('"backlog"', self.calls[0]["prompt"])
