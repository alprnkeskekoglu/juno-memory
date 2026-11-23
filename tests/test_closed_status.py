import os
import tempfile
import unittest
from typing import Any

from juno.core import dashboard_collect, threadscan, vaultfmt

WORDS = ("kapandı", "Kapandı", "KAPANDI", "Bitti", "tamamlandı", "Çözüldü", "İPTAL", "Closed")


class ClosedStatus(unittest.TestCase):
    def test_closed_words_in_any_case_and_script(self) -> None:
        for word in (*WORDS, "done", "dropped", "archived", "cancelled", "resolved"):
            self.assertTrue(vaultfmt.is_finished(word), word)
        for word in ("", "open", "in-progress", "in-review"):
            self.assertFalse(vaultfmt.is_finished(word), word)

    def test_thread_signals_treat_turkish_closed_tickets_as_closed(self) -> None:
        for word in WORDS:
            found = threadscan.ticket_signals(["ABC-1"], lambda t: True, lambda t, w=word: w)
            self.assertEqual(len(found), 1, word)
        self.assertEqual(threadscan.ticket_signals(["ABC-1"], lambda t: True, lambda t: "open"), [])

    def test_dashboard_experiments_skip_closed_tickets(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "tasks"))
            for key, word in (("ABC-1", "kapandı"), ("ABC-2", "Bitti"), ("ABC-3", "open")):
                with open(os.path.join(root, "tasks", f"{key}.md"), "w", encoding="utf-8") as h:
                    h.write(f"---\nstatus: {word}\ntitle: T\n---\n")
            none: Any = None
            src = dashboard_collect.Sources(root, root, root, root, none)
            found = dashboard_collect.experiments_tasks(src)["items"]
        self.assertEqual([i["key"] for i in found], ["ABC-3"])


if __name__ == "__main__":
    unittest.main()
