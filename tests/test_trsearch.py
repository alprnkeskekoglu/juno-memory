import json
import os
import unittest
from typing import Any

from juno.adapters import indexquery
from juno.core import trstem
from tests.support import SandboxCase


class Candidates(unittest.TestCase):
    def test_stem_rules(self) -> None:
        self.assertIn("devir", trstem.candidates("devri"))
        self.assertIn("kitap", trstem.candidates("kitabi"))
        self.assertIn("karar", trstem.candidates("kararlari"))
        self.assertIn("agiz", trstem.candidates("agzi"))
        self.assertIn("burun", trstem.candidates("burnu"))
        self.assertIn("ogul", trstem.candidates("oglu"))

    def test_short_stems_are_never_prefixes(self) -> None:
        self.assertEqual(trstem.candidates("evler"), [])
        self.assertFalse([c for c in trstem.candidates("elde") if len(c) >= 4])
        self.assertTrue(all(len(c) >= trstem.STEM_MIN for c in trstem.candidates("evde")))
        self.assertEqual(trstem.stem_term("evd"), '"evd"')
        self.assertEqual(trstem.stem_term("devir"), '"devir"*')

    def test_no_vowel_drop_before_a_consonant_suffix_or_a_stop_cluster(self) -> None:
        self.assertNotIn("kitapilar", trstem.candidates("kitaplar"))
        self.assertFalse([c for c in trstem.candidates("evde") if len(c) >= 4])


class TurkishSearch(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)
        self.write("notes/devir.md", "# Devir\n\nsprint devir notu\n")
        self.write("notes/kitap.md", "# Kitap\n\nokunacak kitap listesi\n")
        self.write("notes/karar.md", "# Karar\n\nmimari karar kaydi\n")
        self.write("notes/agiz.md", "# Agiz\n\nagiz kokusu notu\n")
        self.write("notes/ev.md", "# Ev\n\nevaluation evening evrak ev\n")
        self.write("notes/mix.md", "# Mix\n\nholdout deployment\n")

    def write(self, rel: str, text: str) -> None:
        path = os.path.join(self.sb.vault, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def search(self, *argv: str) -> dict[str, Any]:
        code, out = self.sb.cli("search", *argv, "--json")
        self.assertEqual(code, 0)
        data: dict[str, Any] = json.loads(out)
        return data

    def paths(self, data: dict[str, Any]) -> list[str]:
        return [r["relpath"] for r in data["results"]]

    def test_inflected_queries_find_the_stem_through_the_fallback(self) -> None:
        for query, rel, stem in [
            ("devrini", "notes/devir.md", "devir"),
            ("kitabı", "notes/kitap.md", "kitap"),
            ("kararları", "notes/karar.md", "karar"),
            ("ağzı", "notes/agiz.md", "agiz"),
        ]:
            with self.subTest(query=query):
                data = self.search(query)
                self.assertIn(rel, self.paths(data))
                self.assertIn(stem, next(iter(data["stems"].values())))
                self.assertEqual(data.get("unmatched", []), [])

    def test_stem_is_announced_in_text(self) -> None:
        out = self.sb.cli("search", "devrini")[1]
        self.assertIn('(searched stems "devir" for "devrini")', out)
        self.assertIn("notes/devir.md", out)

    def test_short_stem_does_not_match_ev_prefixes(self) -> None:
        data = self.search("evde")
        self.assertEqual(data["results"], [])
        self.assertNotIn("stems", data)

    def test_swap_typo_is_still_handled_by_the_typo_fallback(self) -> None:
        data = self.search("devri")
        self.assertEqual(self.paths(data), ["notes/devir.md"])
        self.assertEqual(data["corrections"], {"devri": ["devir"]})
        self.assertNotIn("stems", data)

    def test_query_with_hits_is_unchanged(self) -> None:
        data = self.search("holdout deployment")
        self.assertEqual(self.paths(data), ["notes/mix.md"])
        self.assertNotIn("stems", data)
        db = os.path.join(self.sb.state, "index.sqlite3")
        for query in ("holdout deployment", "devir sprint", "kitap"):
            plain = indexquery.search(db, query, limit=5)
            wide = indexquery.search(db, query, limit=5, stems={}, unmatched=[])
            self.assertTrue(plain)
            self.assertEqual(plain, wide)

    def test_stems_also_widen_the_or_fallback(self) -> None:
        data = self.search("devir kitabı")
        self.assertEqual(sorted(self.paths(data)), ["notes/devir.md", "notes/kitap.md"])
        self.assertEqual(sorted(data["stems"]), ["kitabı"])
        self.assertNotIn("unmatched", data)

    def test_unmatched_terms_in_text_and_json(self) -> None:
        out = self.sb.cli("search", "devir zzzfoo")[1]
        self.assertIn("unmatched: zzzfoo", out)
        data = self.search("devir zzzfoo")
        self.assertEqual(self.paths(data), ["notes/devir.md"])
        self.assertEqual(data["unmatched"], ["zzzfoo"])

    def test_stem_and_typo_matched_terms_are_not_unmatched(self) -> None:
        data = self.search("devrini zzzfoo")
        self.assertEqual(data["unmatched"], ["zzzfoo"])
        typo = self.search("holdotu zzzfoo")
        self.assertEqual(typo["unmatched"], ["zzzfoo"])
        self.assertEqual(typo["corrections"], {"holdotu": ["holdout"]})

    def test_no_unmatched_key_when_all_terms_match(self) -> None:
        self.assertNotIn("unmatched", self.search("devir"))


if __name__ == "__main__":
    unittest.main()
