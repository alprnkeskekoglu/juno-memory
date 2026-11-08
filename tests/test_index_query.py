import time
import unittest

from juno.core import search as core
from tests.support import timing
from tests.test_index import VaultCase


class Queries(VaultCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("notes/a.md", "# Alpha\n\nplain words near or and not here\n")
        self.build()

    def test_syntax_injection_is_inert(self) -> None:
        for q in ['"', 'a"b', "*", "pla*", "NEAR(", "NEAR(a b)", "OR", "AND", "NOT", "-", "-words",
                  "title:alpha", "body : x", "(", ")", "^", "words OR", "{", "' OR 1=1 --", "a:b:c",
                  "\x00", "words\x00"]:  # fmt: skip
            self.assertIsInstance(self.search(q), list, q)
        self.assertEqual(self.paths("title:alpha"), ["notes/a.md"])
        self.assertEqual(self.paths("OR"), ["notes/a.md"])
        self.assertEqual(self.paths("near"), ["notes/a.md"])

    def test_empty_or_punctuation_only(self) -> None:
        for q in ["", "   ", "!!!", '-*:"()', None]:
            self.assertEqual(self.search(q), [])

    def test_limit_validation(self) -> None:
        self.assertEqual(self.search("words", limit=0), [])
        self.assertEqual(self.search("words", limit=-3), [])

    def test_and_then_or_fallback(self) -> None:
        self.assertEqual(self.paths("alpha missingtoken"), ["notes/a.md"])


class Language(VaultCase):
    def test_diacritics_fold_without_preset(self) -> None:
        self.write("notes/tr.md", "# Karar\n\nVeri değiştirme işlemi ŞEHİR çağrısı için yapıldı.\n")
        self.write(
            "notes/en.md", "# Decision\n\nWe are changing the configuration of running jobs.\n"
        )
        self.build()
        self.assertEqual(self.paths("degistirme"), ["notes/tr.md"])
        self.assertEqual(self.paths("değiştirme"), ["notes/tr.md"])
        self.assertEqual(self.paths("change configurations job run"), ["notes/en.md"])
        self.assertIn("değiştirme", self.search("degistirme")[0]["snippet"])

    def test_turkish_preset_folds_dotless_i(self) -> None:
        self.preset = "tr"
        self.write("notes/tr.md", "# Karar\n\nVeri ılık ŞEHİR çağrısı için yapıldı.\n")
        self.build()
        self.assertEqual(self.paths("ILIK sehir cagrisi"), ["notes/tr.md"])
        self.assertEqual(self.paths("şehir"), ["notes/tr.md"])

    def test_snippet_bounded_and_centered(self) -> None:
        self.write(
            "notes/long.md", "# L\n\n" + ("filler " * 120) + "needleword " + ("tail " * 120) + "\n"
        )
        self.build()
        hit = self.search("needleword")[0]
        self.assertLessEqual(len(hit["snippet"]), 300)
        self.assertIn("needleword", hit["snippet"])

    def test_title_and_heading_boost(self) -> None:
        self.write(
            "notes/body.md", "# Misc\n\n" + "rareterm appears in the body only. " + "x " * 20 + "\n"
        )
        self.write("notes/title.md", "# Rareterm guide\n\nunrelated text here\n")
        self.build()
        res = self.paths("rareterm")
        self.assertEqual(res[0], "notes/title.md")
        self.assertEqual(sorted(res), ["notes/body.md", "notes/title.md"])


class Filters(VaultCase):
    def test_repo_and_ticket_filters(self) -> None:
        self.write(
            "tasks/ABC-100.md",
            "---\njira: ABC-100\nrepos: [billing-api, ash]\n---\n# T100\n\nfiltterm one\n",
        )
        self.write("tasks/ABC-200.md", "# T200\n\nfiltterm two\n")
        self.write("projects/ash.md", "---\nrepo: ash\n---\n# ash\n\nfiltterm three\n")
        self.write("rules/webapp.md", "# rules\n\nfiltterm four\n")
        self.write(
            "handoffs/ABC-300.md",
            "# H\n\n## 2026-10-19 10:00 · ABC-300 · x · abcd1234\nRepos: webapp, ash\nTickets: ABC-300, ABC-100\nfiltterm done\n",
        )
        self.build()
        self.assertEqual(len(set(self.paths("filtterm", limit=20))), 5)
        self.assertEqual(
            set(self.paths("filtterm", ticket="ABC-100", limit=20)),
            {"tasks/ABC-100.md", "handoffs/ABC-300.md"},
        )
        self.assertEqual(
            set(self.paths("filtterm", ticket="abc-200", limit=20)), {"tasks/ABC-200.md"}
        )
        self.assertEqual(
            set(self.paths("filtterm", repo="ASH", limit=20)),
            {"tasks/ABC-100.md", "projects/ash.md", "handoffs/ABC-300.md"},
        )
        self.assertEqual(self.paths("filtterm", repo="ash", ticket="ABC-200"), [])
        self.assertEqual(self.paths("filtterm", repo="ash[", ticket="x'--"), [])
        self.assertEqual(self.paths("ABC-200"), ["tasks/ABC-200.md"])


class Typos(VaultCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("notes/a.md", "# Alpha\n\nholdout group kural deployment\n")
        self.write("notes/b.md", "# Beta\n\nkurul meeting\n")
        self.build()

    def fixed(self, query: str) -> tuple[list[str], dict[str, list[str]]]:
        found: dict[str, list[str]] = {}
        paths = [r["relpath"] for r in self.search(query, corrections=found)]
        return paths, found

    def test_transposition_is_corrected(self) -> None:
        self.assertEqual(self.fixed("holdotu"), (["notes/a.md"], {"holdotu": ["holdout"]}))

    def test_insertion_and_deletion_are_corrected(self) -> None:
        self.assertEqual(self.fixed("holdoutt"), (["notes/a.md"], {"holdoutt": ["holdout"]}))
        self.assertEqual(self.fixed("holdut"), (["notes/a.md"], {"holdut": ["holdout"]}))

    def test_substitution_is_not_corrected(self) -> None:
        self.assertEqual(self.fixed("holdaut"), ([], {}))
        self.assertEqual(self.fixed("kurxl"), ([], {}))
        self.assertEqual(self.fixed("kurul"), (["notes/b.md"], {}))

    def test_matching_token_is_not_expanded(self) -> None:
        self.assertEqual(self.fixed("holdout deployment"), (["notes/a.md"], {}))

    def test_corrected_token_joins_and_match(self) -> None:
        self.assertEqual(
            self.fixed("holdotu deployment"), (["notes/a.md"], {"holdotu": ["holdout"]})
        )

    def test_short_numeric_and_unknown_tokens_are_left_alone(self) -> None:
        self.assertEqual(self.fixed("hol"), ([], {}))
        self.assertEqual(self.fixed("zzzzzzz"), ([], {}))
        self.write("notes/n.md", "# N\n\nticket 155141\n")
        self.build()
        self.assertEqual(self.fixed("155411"), ([], {}))

    def test_turkish_fold_applies_under_the_preset_only(self) -> None:
        self.preset = "tr"
        self.write("notes/t.md", "# T\n\nkarşılaştırma\n")
        self.build()
        self.assertEqual(self.fixed("KARŞILAŞTIRMA")[1], {})
        self.assertEqual(self.fixed("karşılatırma")[1], {"karşilatirma": ["karsilastirma"]})

    def test_default_search_is_unchanged(self) -> None:
        self.assertEqual(self.search("holdotu"), [])


CARD = "## 2026-10-25 23:24 · - · holdout card work · 1565b348\nRepos: x\nholdout card body text\n"


class DedupeSpread(VaultCase):
    def test_same_card_in_two_handoff_files_returns_once(self) -> None:
        self.write("handoffs/maestro.md", "# Handoffs\n\n" + CARD)
        self.write("handoffs/juno.md", "# Handoffs\n\n" + CARD)
        self.build()
        self.assertEqual(len(self.paths("holdout")), 1)

    def test_same_card_under_different_file_titles_returns_once(self) -> None:
        self.write("handoffs/maestro.md", CARD)
        self.write("handoffs/work.md", "# Handoffs · work\n\n" + CARD)
        self.write("handoffs/test-report.md", "# Handoffs · test-report\n\n" + CARD)
        self.build()
        self.assertEqual(len(self.paths("holdout")), 1)

    def test_later_passages_of_a_shared_card_do_not_return(self) -> None:
        filler = " ".join(f"word{i}" for i in range(60))
        card = (
            "## 2026-10-25 23:24 · - · long card · 2565b348\nRepos: x\n\nholdout first part "
            + filler
            + "\n\nholdout card second part "
            + filler
            + "\n\nholdout card third part "
            + filler
            + "\n"
        )
        for name in ("maestro", "juno", "work"):
            self.write(f"handoffs/{name}.md", f"# Handoffs · {name}\n\n" + card)
        self.build()
        self.assertEqual(len(self.paths("holdout card")), 1)

    def test_whitespace_differences_still_dedupe(self) -> None:
        self.write("notes/a.md", "# A\n\nholdout   card\nbody\n")
        self.write("notes/b.md", "# B\n\nholdout card body\n")
        self.build()
        self.assertEqual(len(self.paths("holdout")), 1)

    def test_distinct_passages_in_two_files_both_return(self) -> None:
        self.write(
            "handoffs/a.md", "# H\n\n## 2026-10-25 23:24 · - · one · aaaaaaaa\nholdout one\n"
        )
        self.write(
            "handoffs/b.md", "# H\n\n## 2026-10-25 23:25 · - · two · bbbbbbbb\nholdout two\n"
        )
        self.build()
        self.assertEqual(sorted(self.paths("holdout")), ["handoffs/a.md", "handoffs/b.md"])

    def test_same_body_under_different_card_headings_both_return(self) -> None:
        self.write(
            "handoffs/a.md", "# H\n\n## 2026-10-25 23:24 · - · one · aaaaaaaa\nholdout same\n"
        )
        self.write(
            "handoffs/b.md", "# H\n\n## 2026-10-25 23:25 · - · two · bbbbbbbb\nholdout same\n"
        )
        self.build()
        self.assertEqual(len(self.paths("holdout")), 2)

    def test_tie_prefers_ticket_file_copy(self) -> None:
        self.write("handoffs/aaa.md", "# H\n\n" + CARD)
        self.write("handoffs/ABC-1.md", "# H\n\n" + CARD)
        self.build()
        self.assertEqual(self.paths("holdout"), ["handoffs/ABC-1.md"])

    def many_handoffs(self) -> None:
        for i in range(8):
            self.write(
                f"handoffs/h{i}.md",
                f"# H\n\n## 2026-10-25 23:2{i} · - · c · aaaaaaa{i}\nholdout holdout holdout card {i}\n",
            )
        self.write("knowledge/k.md", "# K\n\nholdout once\n")
        self.write("projects/p.md", "# P\n\nholdout once more here\n")
        self.build()

    def test_dominant_kind_still_leaves_room_for_others(self) -> None:
        self.many_handoffs()
        self.assertNotIn("knowledge/k.md", self.paths("holdout"))
        spread = self.paths("holdout", spread=True)
        self.assertIn("knowledge/k.md", spread)
        self.assertIn("projects/p.md", spread)

    def test_no_kind_exceeds_half_the_limit_while_others_remain(self) -> None:
        self.many_handoffs()
        for limit in (2, 3, 4, 5):
            kinds = [p.split("/")[0] for p in self.paths("holdout", spread=True, limit=limit)]
            self.assertLessEqual(kinds.count("handoffs"), -(-limit // 2))
            self.assertEqual(len(kinds), limit)

    def test_cap_relaxes_when_no_other_kind_remains(self) -> None:
        for i in range(4):
            self.write(f"handoffs/h{i}.md", f"# H\n\nholdout card {i}\n")
        self.build()
        self.assertEqual(len(self.paths("holdout", spread=True, limit=4)), 4)

    def test_spread_output_stays_in_score_order(self) -> None:
        self.many_handoffs()
        scores = [r["score"] for r in self.search("holdout", spread=True)]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_ticket_hits_stay_first(self) -> None:
        self.many_handoffs()
        self.write("tasks/ABC-9.md", "# T\n\nunrelated words\n")
        self.build()
        self.assertEqual(self.paths("ABC-9 holdout", spread=True)[0], "tasks/ABC-9.md")

    def test_limit_one_is_unchanged(self) -> None:
        self.many_handoffs()
        self.assertEqual(
            self.paths("holdout", spread=True, limit=1), self.paths("holdout", limit=1)
        )

    def test_spread_is_not_the_default(self) -> None:
        self.many_handoffs()
        self.assertEqual(len([p for p in self.paths("holdout") if p.startswith("handoffs/")]), 5)


class QueryNoise(VaultCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("tasks/ABC-222222.md", "# ABC-222222\n\nplato reference data\n")
        self.write(
            "handoffs/ABC-222222.md",
            "# Handoffs\n\n## 2026-10-19 10:58 · ABC-222222 · plan · c6a4d4e8\nqa round\n",
        )
        self.write(
            "handoffs/misc.md",
            "# misc\n\nmaestro retro maestro retro maestro retro\nTickets: ABC-222222\n",
        )
        self.write("notes/enigma.md", "# enigma\n\ndispatcher example slack archives com\n")
        self.build()

    def test_ticket_key_puts_ticket_files_first(self) -> None:
        res = self.search("/maestro:retro ABC-222222")
        self.assertEqual(
            [r["relpath"] for r in res[:2]], ["tasks/ABC-222222.md", "handoffs/ABC-222222.md"]
        )
        self.assertEqual([r["score"] for r in res[:2]], [core.TICKET_SCORE, core.TICKET_SCORE])
        self.assertEqual(len({r["relpath"] for r in res}), len(res))
        self.assertEqual(self.paths("ABC-222222 plan", limit=1), ["tasks/ABC-222222.md"])

    def test_ticket_files_first_only_when_enabled(self) -> None:
        res = self.search("ABC-222222", ticket_files=False, limit=10)
        self.assertNotIn(core.TICKET_SCORE, [r["score"] for r in res])

    def test_ticket_hits_respect_filters_and_unknown_keys(self) -> None:
        self.assertEqual(self.paths("ABC-222222", repo="ash"), [])
        self.assertEqual(self.paths("INS-202610210802"), [])
        self.assertNotIn(
            core.TICKET_SCORE, [r["score"] for r in self.search("ABC-2222220 maestro")]
        )

    def test_urls_are_not_searched(self) -> None:
        self.assertEqual(
            self.paths(
                "https://dispatcher-example.slack.com/archives/D0AAAAAAAAA/p1792659366028639"
            ),
            [],
        )
        self.assertEqual(self.paths("bak www.example.com/archives"), [])
        self.assertEqual(core.query_tokens("see https://a.b/c?x=1 now"), ["see", "now"])

    def test_stub_cards_never_match(self) -> None:
        self.write(
            "handoffs/maestro.md",
            "# Handoffs\n\n## 2026-10-22 19:15 · - · stub · 11efb8c4\nauto: true\nRepos: maestro\nTickets: ABC-222222\nzorkstub\n",
        )
        self.write(
            "handoffs/real.md",
            "# Handoffs\n\n## 2026-10-22 19:15 · - · real work · 22efb8c4\nzorkstub\n",
        )
        self.build()
        self.assertEqual(self.paths("zorkstub"), ["handoffs/real.md"])

    def test_ticket_in_url_still_found(self) -> None:
        self.assertEqual(
            self.paths("https://example.atlassian.net/browse/ABC-222222", limit=2),
            ["tasks/ABC-222222.md", "handoffs/ABC-222222.md"],
        )


class Performance(VaultCase):
    @timing
    def test_few_hundred_files(self) -> None:
        import random

        rng = random.Random(7)
        vocab = [f"w{i}" for i in range(2000)] + [
            "değiştirme",
            "configuration",
            "deploy",
            "rollback",
        ]
        for i in range(300):
            sections = []
            for s in range(4):
                words = " ".join(rng.choice(vocab) for _ in range(90))
                sections.append(f"## Section {s} topic{rng.randint(0, 50)}\n\n{words}\n")
            key = f"ABC-{100000 + i}"
            self.write(
                f"tasks/{key}.md" if i % 2 else f"notes/n{i}.md",
                f"---\nstatus: active\njira: {key}\nrepos: [repo{i % 20}]\n---\n# Title {i}\n\n"
                + "\n".join(sections),
            )
        cold = self.build()
        warm = self.build()
        self.assertEqual((cold["reindexed"], warm["reindexed"]), (300, 0))
        queries = [
            "deploy rollback",
            "değiştirme configuration",
            "w17 w512",
            "topic7",
            "ABC-100500",
            "nothing matches xyzzy",
        ]
        times = []
        for n in range(60):
            started = time.perf_counter()
            self.search(queries[n % len(queries)], repo="repo3" if n % 5 == 0 else None)
            times.append((time.perf_counter() - started) * 1000)
        times.sort()
        self.assertLess(warm["ms"], 500)
        self.assertLess(times[int(len(times) * 0.95) - 1], 100)


if __name__ == "__main__":
    unittest.main()
