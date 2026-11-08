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


if __name__ == "__main__":
    unittest.main()
