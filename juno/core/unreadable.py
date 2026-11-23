from collections.abc import Sequence

SHOWN = 3


def describe(paths: Sequence[str], shown: int = SHOWN) -> str:
    """One line for files that exist but could not be read; empty when there are none."""
    if not paths:
        return ""
    noun = "file" if len(paths) == 1 else "files"
    text = f"{len(paths)} {noun} could not be read: {', '.join(paths[:shown])}"
    if len(paths) > shown:
        text += f" (and {len(paths) - shown} more)"
    return text
