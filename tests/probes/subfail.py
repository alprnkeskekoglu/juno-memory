import unittest


class Probe(unittest.TestCase):
    def test_one_subtest_fails(self) -> None:
        for n in (1, 2):
            with self.subTest(n=n):
                self.assertEqual(n, 1, "deliberate")
