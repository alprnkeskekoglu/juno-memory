import argparse
from collections.abc import Mapping

from juno import config
from juno.adapters import learned, ledgerio
from juno.cmd import write
from juno.core.errors import Refused


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true")


def rebuild(cfg: config.Config) -> int:
    count = learned.rebuild(cfg, write.VIEW_LOCK_SECONDS)
    if count < 0:
        raise Refused("the vault is busy (vault.lock held); retry in a moment")
    return count


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    def body(cfg: config.Config) -> int:
        seg = write.guard(cfg)
        count = rebuild(cfg)
        ledgerio.record(cfg, seg, {"ev": "digest"})
        write.emit(
            args, {"entries": count}, f"Juno: _generated/learned.md rebuilt with {count} entries."
        )
        return 0

    return write.execute(args, env, body)
