import re

FLOOR = (3, 14)
VERSION_RE = re.compile(r"\((\d+),\s*(\d+)")
PY_NAME_RE = re.compile(r"python3(\.\d+)?$")

Version = tuple[int, int]
Source = tuple[str, list[str]]


def parse_version(output: str) -> Version | None:
    match = VERSION_RE.search(output)
    return (int(match.group(1)), int(match.group(2))) if match else None


def is_python_name(path: str) -> bool:
    return bool(PY_NAME_RE.search(path.rsplit("/", 1)[-1]))


def parse_uv_list(output: str) -> list[str]:
    found: list[str] = []
    for line in output.splitlines():
        for token in line.split():
            if token.startswith("/") and is_python_name(token):
                found.append(token)
                break
    return found


def pick(
    sources: list[tuple[str, list[tuple[str, Version | None]]]],
    floor: Version = FLOOR,
) -> tuple[str, Version] | None:
    for _name, entries in sources:
        eligible = [(path, ver) for path, ver in entries if ver is not None and ver >= floor]
        if eligible:
            best = max(eligible, key=lambda item: item[1])
            return best
    return None


def refusal(path: str, version: Version | None) -> str:
    seen = "unreadable" if version is None else f"{version[0]}.{version[1]}"
    return f"Juno needs Python >= {FLOOR[0]}.{FLOOR[1]}; {path} is {seen}"
