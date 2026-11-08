import unittest

from juno.core import dashboard, dashboard_parse, vaultfmt
from juno.core import dashboard_fmt as fmt
from juno.core import dashboard_text as texts


class Parsing(unittest.TestCase):
    def test_frontmatter_scalars_inline_and_block_lists(self) -> None:
        fields, body = vaultfmt.parse_frontmatter(
            "---\ntitle: 'Hi'\ntags: [a, b]\nrepos:\n  - x\n  - y\nstatus: next\n---\nrest\n"
        )
        self.assertEqual(
            fields, {"title": "Hi", "tags": ["a", "b"], "repos": ["x", "y"], "status": "next"}
        )
        self.assertEqual(body, "rest\n")
        self.assertEqual(vaultfmt.parse_frontmatter("no front")[0], {})

    def test_threads_group_by_ticket_then_tag_and_carry_their_age(self) -> None:
        ticket = dashboard.ticket_pattern(["ABC"])
        items = dashboard_parse.parse_threads(
            "- [ABC-7, app] first · since 2026-10-15\n- [abc-7] second · since 2026-10-20\n"
            "- [nova, other] third\n- [] bad\n",
            "2026-10-25",
            ticket,
        )
        self.assertEqual(
            [(i["group"], i["age"]) for i in items], [("ABC-7", 10), ("ABC-7", 5), ("nova", None)]
        )
        plain = dashboard_parse.parse_threads("- [ABC-7] x\n", "2026-10-25", None)
        self.assertEqual(plain[0]["group"], "ABC-7")
        self.assertIsNone(dashboard.ticket_pattern([]))

    def test_thread_is_due_on_its_latest_date_including_yearless_ones(self) -> None:
        items = dashboard_parse.parse_threads(
            "- [app] gate failed 2026-10-25; rerun ~10-28 with sample · since 2026-10-19\n"
            "- [nova] no date here · since 2026-10-22\n"
            "- [x] old 2026-10-20 and 13-45 is no date · since 2026-10-01\n",
            "2026-10-25",
            None,
        )
        self.assertEqual(
            [(i["due"], i["due_state"]) for i in items],
            [("2026-10-28", "future"), (None, None), ("2026-10-20", "past")],
        )

    def test_yearless_date_before_the_anchor_rolls_to_next_year(self) -> None:
        self.assertEqual(
            dashboard_parse.yearless_dates("check ~01-05", "2026-12-20"), ["2027-01-05"]
        )

    def test_backlog_flags_the_urgent_and_counts_invalid(self) -> None:
        good = "---\ntitle: Later\nstatus: inbox\npriority: p3\nadded: 2026-10-01\nupdated: 2026-10-01\n---\n"
        soon = (
            good.replace("inbox", "next")
            .replace("p3", "p2")
            .replace("added", "due: 2026-10-27\nadded")
        )
        block = dashboard_parse.backlog_block(
            [
                ("p3-later", good),
                ("p2-soon", soon),
                ("bad-one", "no frontmatter"),
                ("done-it", good.replace("inbox", "done")),
            ],
            "2026-10-25",
            40,
        )
        self.assertEqual(
            {i["slug"]: i["urgent"] for i in block["items"]}, {"p2-soon": True, "p3-later": False}
        )
        self.assertEqual((block["open"], block["invalid"]), (2, 1))


class Formatting(unittest.TestCase):
    def test_turkish_and_english_numbers_and_dates(self) -> None:
        tr, en = texts.Text("tr"), texts.Text("none")
        self.assertEqual(
            (fmt.money(3380.91, tr), fmt.money(95.4, tr), fmt.money(2.5, tr), fmt.money(0.002, tr)),
            ("$3 381", "$95", "$2,50", "$0,002"),
        )
        self.assertEqual(
            (fmt.money(3380.91, en), fmt.money(2.5, en), fmt.money(0.0, en)),
            ("$3,381", "$2.50", "$0.00"),
        )
        self.assertEqual((fmt.percent(0.26, tr), fmt.percent(0.001, tr, 5)), ("%26", "%<1"))
        self.assertEqual((fmt.percent(0.26, en), fmt.percent(None, en)), ("26%", "no data"))
        self.assertEqual(
            (fmt.short_date("2026-10-19", tr), fmt.short_date("2026-10-19", en)),
            ("19 Eki", "19 Oct"),
        )
        self.assertEqual(fmt.model_name("claude-opus-5-5-20260301[1m]"), "Opus 5.5")

    def test_text_tables_have_the_same_keys(self) -> None:
        self.assertEqual(sorted(texts.EN), sorted(texts.TR))
        for key, value in texts.EN.items():
            self.assertEqual(
                value.count("%s") + value.count("%d"),
                texts.TR[key].count("%s") + texts.TR[key].count("%d"),
                key,
            )


if __name__ == "__main__":
    unittest.main()


class SharedParsers(unittest.TestCase):
    def test_trailing_comment_and_thread_since_follow_the_vault_parsers(self) -> None:
        item = (
            "---\ntitle: Commented\nstatus: next # why\npriority: p2\nadded: 2026-10-01\n"
            "updated: 2026-10-01\n---\n"
        )
        block = dashboard_parse.backlog_block([("commented", item)], "2026-10-02", 5)
        self.assertEqual((block["open"], block["invalid"]), (1, 0))
        items = dashboard_parse.parse_threads(
            "- [app] ship it 2026-10-30 · since 2026-10-01\n", "2026-10-25", None
        )
        self.assertEqual((items[0]["since"], items[0]["due"]), ("2026-10-01", "2026-10-30"))
