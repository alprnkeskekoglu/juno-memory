import re
from collections.abc import Callable, Mapping

CODE_SCHEMA_VERSION = 1

Step = Callable[[str], None]
SCHEMA_RE = re.compile(r"^schema_version\s*=\s*(\d+)\s*$", re.M)

STEPS: dict[int, Step] = {}


def read_schema(toml_text: str) -> int | None:
    match = SCHEMA_RE.search(toml_text)
    return int(match.group(1)) if match else None


def write_schema(toml_text: str, version: int) -> str:
    if SCHEMA_RE.search(toml_text):
        return SCHEMA_RE.sub(f"schema_version = {version}", toml_text, count=1)
    return f"schema_version = {version}\n" + toml_text


def plan(current: int, target: int, steps: Mapping[int, Step]) -> list[int]:
    wanted = list(range(current, target))
    missing = [v for v in wanted if v not in steps]
    if missing:
        raise ValueError(f"no migration step from schema {missing[0]} to {missing[0] + 1}")
    return wanted
