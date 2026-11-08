import argparse
from collections.abc import Mapping

from juno import __version__


def add_arguments(parser: argparse.ArgumentParser) -> None:
    del parser


def run(_args: argparse.Namespace, _env: Mapping[str, str]) -> int:
    print(f"juno {__version__}")
    return 0
