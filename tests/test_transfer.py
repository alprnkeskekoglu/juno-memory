import io
import json
import os
import stat
import tarfile
import unittest

from juno.core import archive
from tests.support import Sandbox, SandboxCase
from tests.test_init import tree


def write_tar(path: str, members: list[tuple[tarfile.TarInfo, bytes | None]]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(archive.INFO_NAME)
        data = b"{}"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        for member, payload in members:
            tar.addfile(member, io.BytesIO(payload) if payload is not None else None)


def file_member(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


class MemberRules(unittest.TestCase):
    def test_rules(self) -> None:
        self.assertIsNone(archive.member_error("vault/a.md", "file"))
        self.assertIsNone(archive.member_error("juno-export.json", "file"))
        for name in ("/etc/passwd", "vault/../x", "../x", "other/x", "vault\\x"):
            self.assertIsNotNone(archive.member_error(name, "file"), name)
        self.assertIsNotNone(archive.member_error("vault/x", "other"))
        self.assertIsNone(archive.member_error("vault/a/l", "symlink", "../b"))
        self.assertIsNotNone(archive.member_error("vault/l", "symlink", "/etc"))
        self.assertIsNotNone(archive.member_error("vault/l", "symlink", "../../x"))
        self.assertIsNotNone(archive.member_error("vault/l", "symlink", "../config/x"))


class RoundTrip(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)
        with open(
            os.path.join(self.sb.vault, "notes", "2026-01-01-a.md"), "w", encoding="utf-8"
        ) as h:
            h.write("note body\n")
        os.makedirs(os.path.join(self.sb.state, "eval"))
        with open(os.path.join(self.sb.state, "shadow.jsonl"), "w", encoding="utf-8") as h:
            h.write('{"a": 1}\n')
        os.makedirs(os.path.join(self.sb.state, "cards"))
        with open(
            os.path.join(self.sb.state, "cards", "candidates.json"), "w", encoding="utf-8"
        ) as h:
            h.write("[]")
        self.out = os.path.join(self.sb.root, "export.tar.gz")

    def other(self) -> Sandbox:
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        return sb

    def test_export_contents_and_mode(self) -> None:
        code, out = self.sb.cli("export", "--out", self.out)
        self.assertEqual(code, 0, out)
        self.assertEqual(stat.S_IMODE(os.stat(self.out).st_mode), 0o600)
        with tarfile.open(self.out) as tar:
            names = tar.getnames()
            config_text = tar.extractfile("config/juno.toml")
            assert config_text is not None
            self.assertNotIn("[runtime]", config_text.read().decode())
        self.assertIn("vault/core.md", names)
        self.assertTrue(any(n.startswith("vault/.git/") for n in names))
        self.assertFalse(any(n.startswith("state/") for n in names))

    def test_with_state(self) -> None:
        self.sb.cli("export", "--out", self.out, "--with-state")
        with tarfile.open(self.out) as tar:
            names = tar.getnames()
        for expected in ("state/shadow.jsonl", "state/eval", "state/cards/candidates.json"):
            self.assertIn(expected, names)

    def test_import_into_fresh_machine(self) -> None:
        self.sb.cli("export", "--out", self.out, "--with-state")
        fresh = self.other()
        code, out = fresh.cli("import", self.out, *fresh.install_args())
        self.assertEqual(code, 0, out)
        before = tree(self.sb.vault)
        self.assertEqual(tree(fresh.vault), before)
        self.assertTrue(os.path.isdir(os.path.join(fresh.vault, ".git")))
        with open(os.path.join(fresh.state, "shadow.jsonl"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"a": 1}\n')
        with open(fresh.config, encoding="utf-8") as handle:
            self.assertTrue(handle.read().startswith("[runtime]"))
        self.assertTrue(os.access(os.path.join(fresh.bin, "juno"), os.X_OK))
        settings = json.loads(fresh.read_settings() or b"")
        self.assertIn(fresh.vault, settings["permissions"]["additionalDirectories"])

    def test_import_refuses_non_empty_vault(self) -> None:
        self.sb.cli("export", "--out", self.out)
        fresh = self.other()
        os.makedirs(fresh.vault)
        keep = os.path.join(fresh.vault, "mine.md")
        with open(keep, "w", encoding="utf-8") as handle:
            handle.write("x")
        code, out = fresh.cli("import", self.out, *fresh.install_args())
        self.assertEqual(code, 2)
        self.assertIn("not empty", out)
        self.assertEqual(os.listdir(fresh.vault), ["mine.md"])

    def test_import_keeps_existing_config(self) -> None:
        self.sb.cli("export", "--out", self.out)
        fresh = self.other()
        os.makedirs(os.path.dirname(fresh.config))
        with open(fresh.config, "w", encoding="utf-8") as handle:
            handle.write("[caps]\ncore_chars = 3000\n")
        code, out = fresh.cli("import", self.out, *fresh.install_args())
        self.assertEqual(code, 0, out)
        with open(fresh.config, encoding="utf-8") as handle:
            self.assertIn("core_chars = 3000", handle.read())

    def test_export_without_vault(self) -> None:
        fresh = self.other()
        self.assertEqual(fresh.cli("export", "--out", os.path.join(fresh.root, "x.tgz"))[0], 2)

    def test_malicious_archives_refused_before_writing(self) -> None:
        link = tarfile.TarInfo("vault/escape")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc"
        cases = {
            "dotdot": [file_member("vault/../../evil.txt", b"x")],
            "absolute": [file_member("/tmp/evil.txt", b"x")],
            "symlink": [(link, None)],
            "toplevel": [file_member("stray.txt", b"x")],
        }
        for name, members in cases.items():
            with self.subTest(name):
                fresh = self.other()
                bad = os.path.join(fresh.root, "bad.tgz")
                write_tar(bad, members)
                code, out = fresh.cli("import", bad, *fresh.install_args())
                self.assertEqual(code, 2, out)
                self.assertFalse(os.path.exists(fresh.vault))
                self.assertFalse(os.path.exists(os.path.join(fresh.root, "evil.txt")))

    def test_not_a_juno_export(self) -> None:
        fresh = self.other()
        plain = os.path.join(fresh.root, "plain.tgz")
        with tarfile.open(plain, "w:gz") as tar:
            info = tarfile.TarInfo("vault/a.md")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        self.assertEqual(fresh.cli("import", plain, *fresh.install_args())[0], 2)


if __name__ == "__main__":
    unittest.main()
