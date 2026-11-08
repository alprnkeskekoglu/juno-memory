import os
from collections.abc import Mapping

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCAL_FILE = ".juno-banned.txt"


def read_list(path: str) -> tuple[str, ...]:
    try:
        with open(path, encoding="utf-8") as handle:
            lines = [line.strip() for line in handle]
    except OSError:
        return ()
    return tuple(line.casefold() for line in lines if line and not line.startswith("#"))


def local_banned(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    chosen = (os.environ if env is None else env).get("JUNO_BANNED_FILE")
    return read_list(chosen or os.path.join(REPO, LOCAL_FILE))
