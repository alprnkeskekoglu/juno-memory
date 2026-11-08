import unittest

from tests.support import timing


class Probe(unittest.TestCase):
    def test_plain(self) -> None:
        pass

    @timing
    def test_timing(self) -> None:
        pass
