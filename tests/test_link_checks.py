import unittest

from juno.core import doctor_vault as dv
from juno.core import links


def lines(files: dict[str, str], level: str | None = None) -> list[str]:
    return [f"{lv} {d}" for lv, _, d in dv.link_checks(files) if level in (None, lv)]


def joined(files: dict[str, str]) -> str:
    return "\n".join(lines(files))


class Refs(unittest.TestCase):
    def test_markdown_links_resolve_from_the_source_directory(self) -> None:
        found = links.refs("notes/a.md", "[x](b.md) [y](../knowledge/k.md#Top) [z](/core.md)")
        self.assertEqual(
            [(r.path, r.frag) for r in found],
            [("notes/b.md", ""), ("knowledge/k.md", "Top"), ("core.md", "")],
        )

    def test_urls_images_non_md_code_and_top_are_ignored(self) -> None:
        text = (
            "[a](https://x.io/a.md) [b](mailto:a@b.c) [c](//cdn/x.md) ![i](p.md) [d](p.png)\n"
            "[e](#top) [f]() `[g](g.md)`\n```\n[h](h.md)\n```\n"
        )
        self.assertEqual(links.refs("a.md", text), [])

    def test_same_file_anchor_and_titles_and_encoding(self) -> None:
        found = links.refs("a.md", '[a](#Sec) [b](<my%20file.md> "t") [c](x.md?v=1#a%20b)')
        self.assertEqual(
            [(r.path, r.frag) for r in found],
            [("a.md", "Sec"), ("my file.md", ""), ("x.md", "a b")],
        )

    def test_headings_obsidian_and_github(self) -> None:
        found = links.headings(
            "---\n# fm\n---\n# Intro `x`\n## Setup  Steps ##\n## Setup  Steps\n```\n# no\n```\n"
        )
        self.assertTrue(links.has_heading("intro x", found, False))
        self.assertTrue(links.has_heading("SETUP steps", found, False))
        self.assertFalse(links.has_heading("fm", found, False))
        self.assertFalse(links.has_heading("no", found, False))
        self.assertFalse(links.has_heading("setup-steps", found, False))
        self.assertTrue(links.has_heading("setup-steps", found, True))
        self.assertTrue(links.has_heading("setup-steps-1", found, True))
        self.assertTrue(links.has_heading("^block", found, False))


class Checks(unittest.TestCase):
    def test_markdown_links_resolve_and_are_counted(self) -> None:
        files = {
            "notes/a.md": "[b](b.md) [k](../knowledge/k.md#Setup) [r](/core.md) [[core]]",
            "notes/b.md": "# T\n[self](#t)",
            "knowledge/k.md": "## Setup\n",
            "core.md": "[[notes/a]] [[notes/b]]",
        }
        self.assertEqual(
            lines(files, "PASS"), ["PASS 3 wikilinks and 4 markdown links in 4 files resolve"]
        )
        self.assertNotIn("WARN", joined(files))

    def test_broken_markdown_link_is_reported_like_a_wikilink(self) -> None:
        files = {"notes/a.md": "[x](missing.md) [y](../../out.md) [[ghost]]"}
        out = joined(files)
        self.assertIn("WARN broken link notes/a.md:1 -> (missing.md)", out)
        self.assertIn("WARN broken link notes/a.md:1 -> (../../out.md)", out)
        self.assertIn("WARN broken link notes/a.md:1 -> [[ghost]]", out)
        self.assertIn("3 ghost link targets", out)
        self.assertEqual(lines(files, "PASS"), [])

    def test_links_in_code_are_ignored(self) -> None:
        text = "`[x](gone.md)` `[[gone]]`\n```\n[y](gone.md#H)\n[[gone#H]]\n```\n~~~\n[[z]]\n~~~\n"
        self.assertEqual(
            lines({"a.md": text}), ["PASS 0 wikilinks and 0 markdown links in 1 files resolve"]
        )

    def test_missing_heading_is_a_separate_warn(self) -> None:
        files = {
            "a.md": "[[b#Alpha beta]] [[b#nope]] [b](b.md#alpha-beta) [b](b.md#nope) [[#Own]] [[#Gone]]\n# Own",
            "b.md": "## Alpha  Beta\n",
        }
        out = lines(files, "WARN")
        self.assertEqual(
            out,
            [
                "WARN missing heading a.md:1 -> [[b#nope]]",
                "WARN missing heading a.md:1 -> [[#Gone]]",
                "WARN missing heading a.md:1 -> (b.md#nope)",
            ],
        )
        self.assertEqual(
            lines(files, "PASS"), ["PASS 2 wikilinks and 2 markdown links in 2 files resolve"]
        )

    def test_wikilink_heading_does_not_accept_a_github_slug(self) -> None:
        files = {"a.md": "[[b#alpha-beta]]", "b.md": "## Alpha Beta\n"}
        self.assertIn("missing heading a.md:1 -> [[b#alpha-beta]]", joined(files))

    def test_private_and_archived_targets_are_counted_not_named(self) -> None:
        files = {
            "notes/pub.md": "[[secret-plan#Nope]] [s](secret-plan.md#Nope) [[old-thing]] [[pub2]]",
            "notes/pub2.md": "[[pub]]",
            "notes/secret-plan.md": "---\nvisibility: private\n---\n# Plans\n[[pub]]",
            "notes/old-thing.md": "---\nstatus: archived\n---\n",
        }
        out = joined(files)
        self.assertIn("INFO 3 links from visible notes point to private or archived notes", out)
        self.assertNotIn("secret", out)
        self.assertNotIn("old-thing", out)
        self.assertNotIn("WARN", out)

    def test_orphan_count_excludes_structural_files_and_counts_both_link_kinds(self) -> None:
        files = {
            "notes/linked-wiki.md": "",
            "notes/linked-md.md": "",
            "notes/orphan.md": "",
            "knowledge/orphan2.md": "",
            "knowledge/self.md": "[[self]]",
            "ideas/idea.md": "---\nstatus: archived\n---\n",
            "tasks/ABC-1.md": "",
            "handoffs/app.md": "",
            "rules/global.md": "",
            "protocol/p.md": "",
            "core.md": "[[linked-wiki]] [m](notes/linked-md.md)",
            "inbox.md": "",
        }
        out = lines(files, "INFO")
        self.assertEqual(len(out), 1)
        self.assertIn("INFO 3 notes are not linked from any other file", out[0])
        self.assertNotIn("orphan", out[0])

    def test_no_orphan_line_when_everything_is_linked(self) -> None:
        files = {"notes/a.md": "[[b]]", "notes/b.md": "[[a]]"}
        self.assertEqual(lines(files, "INFO"), [])


if __name__ == "__main__":
    unittest.main()
