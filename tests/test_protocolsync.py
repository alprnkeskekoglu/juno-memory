import hashlib
import json
import os
import unittest
from unittest import mock

from juno.core import protocolsync
from tests.support import REPO
from tests.support.write import WriteCase

OLD = os.path.join(REPO, "tests", "fixtures", "protocol-vault-files-v1.md")
NAME = "vault-files.md"


def template(name: str = NAME) -> str:
    with open(os.path.join(REPO, "vault-template", "protocol", name), encoding="utf-8") as handle:
        return handle.read()


def old_text() -> str:
    with open(OLD, encoding="utf-8") as handle:
        return handle.read()


class Core(unittest.TestCase):
    def test_every_template_is_registered_as_shipped(self) -> None:
        names = sorted(os.listdir(os.path.join(REPO, "vault-template", "protocol")))
        self.assertEqual(names, sorted(protocolsync.SHIPPED))
        for name in names:
            self.assertIn(protocolsync.digest(template(name)), protocolsync.SHIPPED[name], name)

    def test_template_says_cap_50_and_names_the_cap_verbs(self) -> None:
        text = template()
        self.assertIn("50 vault writes", text)
        self.assertIn("juno cap show", text)
        self.assertIn("juno cap raise --by N", text)

    def test_template_has_the_thread_close_candidates_rule(self) -> None:
        text = template()
        self.assertIn("Thread close candidates", text)
        (rule,) = [r for r in text.splitlines() if r.startswith("- Thread close candidates")]
        for part in (
            "start of the conversation",
            "AskUserQuestion",
            "multiSelect",
            "juno thread close <ids>",
            "juno thread keep",
            "never close without",
        ):
            self.assertIn(part, rule)
        # core.md (at its budget warning line) already names vault-files.md; the CONTEXT block
        # header points at this rule by name.
        with open(os.path.join(REPO, "vault-template", "core.md"), encoding="utf-8") as handle:
            self.assertIn("protocol/vault-files.md", handle.read())

    def test_classify(self) -> None:
        new, old = template(), old_text()
        self.assertEqual(protocolsync.classify(NAME, new, new), "current")
        self.assertEqual(protocolsync.classify(NAME, new, new + "\n\r\n"), "current")
        self.assertEqual(protocolsync.classify(NAME, new, old), "outdated")
        self.assertEqual(protocolsync.classify(NAME, new, old + "my own note\n"), "modified")
        self.assertEqual(protocolsync.classify(NAME, new, None), "missing")

    def test_digest_ignores_line_ending_and_trailing_blank_lines(self) -> None:
        self.assertEqual(protocolsync.digest("a\r\nb\n\n"), protocolsync.digest("a\nb"))
        self.assertEqual(protocolsync.digest("a"), hashlib.sha256(b"a").hexdigest())


class Sync(WriteCase):
    def setUp(self) -> None:
        super().setUp()
        self.rel = f"protocol/{NAME}"
        self.old = old_text()

    def migrate(self, *flags: str) -> tuple[int, str]:
        return self.juno("migrate", "--protocol", *flags)

    def protocol_checks(self) -> list[tuple[str, str]]:
        out = self.juno("doctor", "--json", *self.sb.install_args())[1]
        return [
            (c["status"], c["detail"]) for c in json.loads(out)["checks"] if c["name"] == "protocol"
        ]

    def test_fresh_vault_reports_nothing_to_do(self) -> None:
        code, out = self.migrate()
        self.assertEqual(code, 0, out)
        self.assertIn("up to date", out)
        self.assertEqual(self.protocol_checks()[0][0], "PASS")

    def test_doctor_reports_outdated_and_modified_differently(self) -> None:
        self.put(self.rel, self.old)
        self.put("protocol/rules.md", "my own rules\n")
        rows = self.protocol_checks()
        self.assertTrue(
            any(s == "WARN" and NAME in d and "migrate --protocol" in d for s, d in rows), rows
        )
        self.assertTrue(any(s == "INFO" and "rules.md" in d and "edited" in d for s, d in rows))

    def test_dry_run_changes_nothing(self) -> None:
        self.put(self.rel, self.old)
        code, out = self.migrate("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn(f"would update {self.rel}", out)
        self.assertEqual(self.get(self.rel), self.old)

    def test_updates_an_unmodified_old_file_with_a_backup(self) -> None:
        self.put(self.rel, self.old)
        code, out = self.migrate()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.get(self.rel), template())
        self.assertIn("juno cap raise --by N", self.get(self.rel))
        found = []
        for root, _, files in os.walk(os.path.join(self.sb.state, "backups")):
            found += [os.path.join(root, f) for f in files if f.endswith("protocol%" + NAME)]
        self.assertEqual(len(found), 1)
        with open(found[0], encoding="utf-8") as handle:
            self.assertEqual(handle.read(), self.old)
        self.assertIn("up to date", self.migrate()[1])

    def test_user_modified_file_is_reported_and_never_overwritten(self) -> None:
        edited = self.old + "\n- my extra rule\n"
        self.put(self.rel, edited)
        code, out = self.migrate()
        self.assertEqual(code, 0, out)
        self.assertIn("edited", out)
        self.assertEqual(self.get(self.rel), edited)

    def test_missing_file_is_restored(self) -> None:
        os.unlink(self.vp(self.rel))
        self.assertEqual(self.migrate()[0], 0)
        self.assertEqual(self.get(self.rel), template())

    def test_symlinked_file_is_left_alone(self) -> None:
        target = os.path.join(self.sb.root, "elsewhere.md")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(self.old)
        os.unlink(self.vp(self.rel))
        os.symlink(target, self.vp(self.rel))
        self.assertEqual(self.migrate()[0], 0)
        self.assertTrue(os.path.islink(self.vp(self.rel)))
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), self.old)

    def test_registered_old_text_is_updated(self) -> None:
        text = "an older shipped protocol text\n"
        known = (*protocolsync.SHIPPED["rules.md"], protocolsync.digest(text))
        self.put("protocol/rules.md", text)
        with mock.patch.dict(protocolsync.SHIPPED, {"rules.md": known}):
            self.assertEqual(self.migrate()[0], 0)
        self.assertEqual(self.get("protocol/rules.md"), template("rules.md"))


if __name__ == "__main__":
    unittest.main()
