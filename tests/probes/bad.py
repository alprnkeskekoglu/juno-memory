import unittest


class Probe(unittest.TestCase):
    def test_fails(self) -> None:
        self.fail("deliberate")
