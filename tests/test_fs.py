import os
import stat
import tempfile
import threading
import unittest

from juno.adapters import fs


class AtomicWrite(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "sub", "f.txt")

    def test_creates_dirs_and_content(self) -> None:
        fs.atomic_write(self.path, b"hello")
        self.assertEqual(fs.read_bytes(self.path), b"hello")

    def test_keeps_existing_mode(self) -> None:
        fs.atomic_write(self.path, b"a", 0o600)
        fs.atomic_write(self.path, b"b")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_explicit_mode(self) -> None:
        fs.atomic_write(self.path, b"a", 0o755)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o755)

    def test_no_temp_files_left(self) -> None:
        fs.atomic_write(self.path, b"a")
        fs.atomic_write(self.path, b"b")
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["f.txt"])

    def test_failure_leaves_old_content_and_no_temp(self) -> None:
        fs.atomic_write(self.path, b"old")
        with self.assertRaises(TypeError):
            fs.atomic_write(self.path, "text")  # type: ignore[arg-type]
        self.assertEqual(fs.read_bytes(self.path), b"old")
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["f.txt"])

    def test_read_missing_is_none(self) -> None:
        self.assertIsNone(fs.read_bytes(self.path))


class Lock(unittest.TestCase):
    def test_second_holder_times_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "vault.lock")
            with fs.locked(path):
                with self.assertRaises(TimeoutError), fs.locked(path, timeout=0.1):
                    pass

    def test_released_after_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "vault.lock")
            with fs.locked(path):
                pass
            with fs.locked(path, timeout=0.1):
                pass

    def test_waits_for_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "vault.lock")
            order: list[str] = []

            def hold() -> None:
                with fs.locked(path):
                    order.append("first")
                    threading.Event().wait(0.15)

            thread = threading.Thread(target=hold)
            thread.start()
            while not order:
                threading.Event().wait(0.01)
            with fs.locked(path, timeout=2):
                order.append("second")
            thread.join()
            self.assertEqual(order, ["first", "second"])


class Logs(unittest.TestCase):
    def test_append_and_rotate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state", "x.log")
            fs.append_log(path, "one", 100)
            fs.append_log(path, "two", 100)
            self.assertEqual(fs.read_bytes(path), b"one\ntwo\n")
            fs.append_log(path, "y" * 200, 100)
            fs.append_log(path, "after", 100)
            self.assertTrue(os.path.exists(path + ".1"))
            self.assertEqual(fs.read_bytes(path), b"after\n")
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)


class PrivateDir(unittest.TestCase):
    def race(self, target: str, workers: int) -> list[BaseException]:
        barrier = threading.Barrier(workers)
        errors: list[BaseException] = []

        def create() -> None:
            barrier.wait()
            try:
                fs.ensure_private_dir(target)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=create) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return errors

    def test_concurrent_creators_all_succeed_with_mode_0700(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for rnd in range(50):
                target = os.path.join(tmp, f"r{rnd}", "a", "b")
                self.assertEqual(self.race(target, 8), [])
                self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o700)

    def test_existing_file_at_path_still_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f")
            with open(path, "w", encoding="utf-8"):
                pass
            with self.assertRaises(FileExistsError):
                fs.ensure_private_dir(path)


class WriteSettings(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def test_plain_file(self) -> None:
        path = os.path.join(self.dir, "s.json")
        with open(path, "wb") as handle:
            handle.write(b"old")
        os.chmod(path, 0o640)
        fs.write_settings(path, b"new")
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"new")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)
        self.assertEqual(os.listdir(self.dir), ["s.json"])

    def test_symlink_stays_symlink(self) -> None:
        os.mkdir(os.path.join(self.dir, "dots"))
        target = os.path.join(self.dir, "dots", "settings.json")
        link = os.path.join(self.dir, "link.json")
        with open(target, "wb") as handle:
            handle.write(b"old")
        os.symlink(target, link)
        fs.write_settings(link, b"new")
        self.assertTrue(os.path.islink(link))
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), b"new")
        self.assertEqual(os.listdir(os.path.join(self.dir, "dots")), ["settings.json"])

    def test_hardlink_keeps_inode(self) -> None:
        a = os.path.join(self.dir, "a.json")
        b = os.path.join(self.dir, "b.json")
        with open(a, "wb") as handle:
            handle.write(b"old")
        os.link(a, b)
        fs.write_settings(a, b"new")
        self.assertEqual(os.stat(a).st_ino, os.stat(b).st_ino)
        self.assertEqual(os.stat(a).st_nlink, 2)
        with open(b, "rb") as handle:
            self.assertEqual(handle.read(), b"new")

    def test_stamp_mismatch_refuses(self) -> None:
        path = os.path.join(self.dir, "s.json")
        with open(path, "wb") as handle:
            handle.write(b"old")
        stamp = fs.stamp(path)
        with open(path, "wb") as handle:
            handle.write(b"edited by someone")
        with self.assertRaises(ValueError):
            fs.write_settings(path, b"new", expect=stamp, check=True)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"edited by someone")

    def test_atomic_write_still_replaces_symlink(self) -> None:
        target = os.path.join(self.dir, "t")
        link = os.path.join(self.dir, "l")
        with open(target, "wb") as handle:
            handle.write(b"old")
        os.symlink(target, link)
        fs.atomic_write(link, b"new")
        self.assertFalse(os.path.islink(link))
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), b"old")


if __name__ == "__main__":
    unittest.main()
