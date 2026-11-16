import contextlib
import time
from collections.abc import Iterator

_steps: dict[str, int] = {}


@contextlib.contextmanager
def step(name: str) -> Iterator[None]:
    begun = time.monotonic()
    try:
        yield
    finally:
        _steps[name] = _steps.get(name, 0) + int((time.monotonic() - begun) * 1000)


def drain() -> dict[str, int]:
    taken = dict(_steps)
    _steps.clear()
    return taken
