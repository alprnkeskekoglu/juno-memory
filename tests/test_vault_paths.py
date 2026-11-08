import os
import unittest

from juno.adapters import cardwrite, gate, store
from juno.cmd import backlog, compact
from juno.core.errors import Refused
from tests.support.write import WriteCase


class OneContainmentCheck(WriteCase):
    def link(self, rel: str, target: str) -> None:
        os.makedirs(os.path.dirname(self.vp(rel)), exist_ok=True)
        os.symlink(target, self.vp(rel))

    def test_plain_paths_resolve_and_odd_ones_do_not(self) -> None:
        self.assertEqual(store.vault_file(self.cfg, "notes/a.md"), self.vp("notes/a.md"))
        self.assertEqual(store.vault_file(self.cfg, "notes", "a.md"), self.vp("notes/a.md"))
        for bad in ("", "../x.md", "notes/../../x.md", "notes/./a.md"):
            self.assertIsNone(store.vault_file(self.cfg, bad), bad)

    def test_symlinked_file_or_directory_is_refused_everywhere(self) -> None:
        outside = os.path.join(self.sb.root, "outside")
        os.makedirs(outside)
        self.link("notes/out", outside)
        self.link("backlog/x.md", os.path.join(outside, "x.md"))
        self.link("handoffs/ABC-1.md", os.path.join(outside, "h.md"))
        self.link("tasks/ABC-1.md", os.path.join(outside, "t.md"))
        self.assertIsNone(store.vault_file(self.cfg, "notes/out/a.md"))
        with self.assertRaises(Refused):
            gate.vault_file(self.cfg, "notes/out/a.md")
        self.assertIsNone(cardwrite.safe_path(self.cfg, "handoffs", "ABC-1.md"))
        self.assertIsNone(cardwrite.safe_path(self.cfg, "tasks", "ABC-1.md"))
        self.assertIsNone(backlog.contained(self.cfg, "backlog", "x.md"))
        self.assertFalse(compact.safe(self.cfg, "notes/out/a.md"))

    def test_a_symlinked_vault_location_still_works(self) -> None:
        real = os.path.join(self.sb.root, "real-vault")
        os.rename(self.sb.vault, real)
        os.symlink(real, self.sb.vault)
        got = store.vault_file(self.cfg, "notes/a.md")
        self.assertEqual(got, os.path.join(real, "notes", "a.md"))
        self.assertTrue(compact.safe(self.cfg, "notes/a.md"))

    def test_case_and_unicode_form_do_not_matter_for_containment(self) -> None:
        self.assertIsNotNone(store.vault_file(self.cfg, "Notes/ǟ.md"))
        self.assertEqual(store.parts_under(self.vp("notes/a.md"), self.sb.vault), ["notes", "a.md"])

    def test_dashboard_out_inside_the_vault_is_refused_through_a_link_or_other_case(self) -> None:
        link = os.path.join(self.sb.root, "to-vault")
        os.symlink(self.sb.vault, link)
        for out in (os.path.join(link, "d.html"), self.vp("d.html")):
            code, _ = self.juno("dashboard", "--out", out)
            self.assertEqual(code, 2, out)
        upper = self.sb.vault.replace("vault", "VAULT")
        if os.path.isdir(upper):
            code, _ = self.juno("dashboard", "--out", os.path.join(upper, "d.html"))
            self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.vp("d.html")))


if __name__ == "__main__":
    unittest.main()
