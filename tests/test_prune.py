import fcntl
import os
import time
import unittest

from juno.adapters import prune, queues
from tests.support.write import WriteCase

OLD = 20 * 86400
SID = "e6113328-a090-4f85-980d-917b3105121a"


class PruneCards(WriteCase):
    def cards(self, name: str) -> str:
        return os.path.join(self.sb.state, "cards", name)

    def make(self, name: str, age: float = OLD) -> str:
        path = self.cards(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{}")
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    def test_queue_locks_are_never_unlinked(self) -> None:
        paths = [
            self.make(q.name + ".lock") for q in (queues.RULES, queues.THREADS, queues.BACKLOG)
        ]
        prune.prune_state(self.cfg)
        self.assertTrue(all(os.path.exists(p) for p in paths))

    def test_a_held_segment_lock_survives_and_a_free_old_one_goes(self) -> None:
        held = self.make(f"{SID}.lock")
        free = self.make("ffffffff-0000-4000-8000-000000000000.lock")
        job = self.make(f"{SID}.job.json")
        fresh = self.make("aaaaaaaa-0000-4000-8000-000000000000.job.json", age=60)
        fd = os.open(held, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            prune.prune_state(self.cfg)
        finally:
            os.close(fd)
        self.assertTrue(os.path.exists(held))
        self.assertFalse(os.path.exists(free))
        self.assertFalse(os.path.exists(job))
        self.assertTrue(os.path.exists(fresh))


if __name__ == "__main__":
    unittest.main()


class PruneTempFiles(WriteCase):
    def test_old_juno_tmp_files_in_state_dirs_go_and_fresh_ones_stay(self) -> None:
        found = {}
        for sub in ("", "ledgers", "status", "cards"):
            for tag, age in (("old", 2 * 86400), ("new", 60)):
                path = os.path.join(self.sb.state, sub, f".juno-tmp-{tag}")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("x")
                stamp = time.time() - age
                os.utime(path, (stamp, stamp))
                found[sub, tag] = path
        prune.prune_state(self.cfg)
        for (sub, tag), path in found.items():
            self.assertEqual(os.path.exists(path), tag == "new", (sub, tag))
