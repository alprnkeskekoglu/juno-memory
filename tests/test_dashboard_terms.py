import re
import unittest

from juno.core import dashboard_panels, dashboard_text
from tests.test_dashboard import DashCase

TERMS = dashboard_panels.GLOSSARY


class Terms(DashCase):
    def seeded(self) -> dict:
        data = self.data()
        data["experiments"]["recall"].update(
            with_evidence=30,
            hits=15,
            pack_hits=26,
            recall=0.5,
            pack_recall=0.87,
            wilson_lower=0.33,
            pack_wilson_lower=0.7,
        )
        data["experiments"]["recall_weekly"] = {
            "weeks": [
                {
                    "week": f"2026-W4{i}",
                    "start": f"2026-10-{12 + 7 * i}",
                    "n": 10,
                    "hits": 5,
                    "pack_hits": 8,
                    "recall": 0.5,
                    "pack_recall": 0.8,
                    "wilson_lower": 0.2,
                    "pack_wilson_lower": 0.5,
                }
                for i in range(2)
            ]
        }
        data["writes"].update(month=2, week=2)
        data["cost"]["juno"]["cards"]["spend"]["2026-10-25"] = 0.4
        data["cost"]["usage"] = {
            "days": [{"day": "2026-10-25", "cost": 3.0}],
            "unpriced": [],
        }
        now = data["generated_epoch"]
        data["arms"] = {
            "items": [
                {
                    "name": n,
                    "status": "running",
                    "started": now - 86400 * 3,
                    "min_n": 60,
                    "max_days": 42,
                    "n": {"on": 3, "off": 4},
                    "ready": False,
                    "reason": "",
                    "stop_fired": False,
                }
                for n in ("linked", "self", "next")
            ]
        }
        data["health"]["rows"] = [
            {"id": i, "label": i, "level": lv, "detail": "d", "fix": None}
            for i, lv in (
                ("runs", "green"),
                ("hook_errors", "green"),
                ("queues", "green"),
                ("stale", "yellow"),
                ("index", "green"),
            )
        ]
        return data

    def test_every_term_has_a_tooltip_in_the_page(self) -> None:
        page = self.page(self.seeded())
        for term in TERMS:
            self.assertIn(f'class="info" data-id="{term}"', page, term)
        self.assertEqual(page.count("<button"), page.count('class="info"'))
        self.assertEqual(page.count('class="info"'), page.count("data-tip=") - self.tip_marks(page))

    @staticmethod
    def tip_marks(page: str) -> int:
        return len(re.findall(r'<g class="col"', page))

    def test_tooltips_are_keyboard_reachable_and_labelled(self) -> None:
        page = self.page(self.seeded())
        for button in re.findall(r"<button [^>]*>i</button>", page):
            self.assertIn('type="button"', button)
            self.assertRegex(button, r'aria-label="[^"]{20,}"')
        self.assertNotIn('tabindex="-1"', page)
        self.assertIn("focusin", page)

    def test_glossary_lists_every_term_once(self) -> None:
        page = self.page(self.seeded())
        gloss = page.split('<details class="card gloss">', 1)[1].split("</details>", 1)[0]
        table = self.sources().t.table
        self.assertIn(f"<h2>{table['sec_gloss']}</h2>", gloss)
        self.assertEqual(gloss.count("<dt>"), len(TERMS))
        for term in TERMS:
            self.assertIn(table["x_" + term], gloss.replace("&#x27;", "'"))

    def test_texts_exist_in_both_languages(self) -> None:
        for term in TERMS:
            for key in ("tn_" + term, "x_" + term):
                self.assertTrue(dashboard_text.EN[key].strip(), key)
                self.assertTrue(dashboard_text.TR[key].strip(), key)
            self.assertNotEqual(dashboard_text.EN["x_" + term], dashboard_text.TR["x_" + term])


class TermsTurkish(Terms):
    preset = "tr"

    def test_turkish_glossary_title(self) -> None:
        page = self.page(self.seeded())
        self.assertIn("<h2>Terimler</h2>", page)
        self.assertIn("en az", page.lower())


if __name__ == "__main__":
    unittest.main()
