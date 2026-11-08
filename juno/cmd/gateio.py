import argparse
import re
from collections.abc import Callable, Mapping

from juno import config
from juno.adapters import gate
from juno.cmd import write
from juno.core.errors import Refused

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def evidence_option(parser: argparse.ArgumentParser, help_text: str | None = None) -> None:
    parser.add_argument(
        "--evidence",
        required=True,
        help=help_text or "words you typed or chose this segment, or repo:<name> / ticket:<KEY>",
    )


def names(raw: str | None, label: str) -> list[str]:
    found: list[str] = []
    for part in (raw or "").split(","):
        name = part.strip()
        if not name:
            continue
        if not NAME_RE.match(name):
            raise Refused(f"{name!r} is not a valid {label} name")
        if name not in found:
            found.append(name)
    return found


def listing(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]"


def report(res: gate.Result, what: str) -> int:
    if res.changed:
        print(f"Juno: {what} {res.rel} (write {res.id}; revert with `juno undo {res.id}`)")
    else:
        print(f"Juno: already recorded in {res.rel}; nothing written")
    return 0


def run_verb(
    args: argparse.Namespace,
    env: Mapping[str, str],
    body: Callable[[config.Config, argparse.Namespace], int],
) -> int:
    return write.execute(args, env, lambda cfg: body(cfg, args))
