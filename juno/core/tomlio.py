import re

HEADER_RE = re.compile(r"^\s*\[")
UNSAFE = ('"', "\\", "\n")


def read_key(text: str, key: str) -> str | None:
    match = re.search(rf'^{re.escape(key)} = "(.*)"$', text, re.M)
    return match.group(1) if match else None


def set_runtime(text: str, python: str, plugin_root: str) -> str:
    for value in (python, plugin_root):
        if any(ch in value for ch in UNSAFE):
            raise ValueError(f"unsupported character in path: {value!r}")
    wanted = {"python": python, "plugin_root": plugin_root}
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "[runtime]"), None)
    if start is None:
        block = ["[runtime]"] + [f'{k} = "{v}"' for k, v in wanted.items()]
        return "\n".join(block) + "\n" + ("\n" + text if text.strip() else "")
    end = next(
        (i for i in range(start + 1, len(lines)) if HEADER_RE.match(lines[i])),
        len(lines),
    )
    body = lines[start + 1 : end]
    for key, value in wanted.items():
        new_line = f'{key} = "{value}"'
        index = next(
            (i for i, line in enumerate(body) if re.match(rf"^{key}\s*=", line)),
            None,
        )
        if index is None:
            body.insert(0 if key == "python" else len(body), new_line)
        else:
            body[index] = new_line
    return "\n".join(lines[: start + 1] + body + lines[end:]) + "\n"


def runtime_is_first_table(text: str) -> bool:
    for line in text.splitlines():
        if HEADER_RE.match(line):
            return line.strip() == "[runtime]"
    return False


def drop_table(text: str, name: str) -> str:
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == f"[{name}]"), None)
    if start is None:
        return text
    end = next(
        (i for i in range(start + 1, len(lines)) if HEADER_RE.match(lines[i])),
        len(lines),
    )
    rest = lines[:start] + lines[end:]
    while rest and not rest[0].strip():
        rest.pop(0)
    return "\n".join(rest) + "\n" if rest else ""
