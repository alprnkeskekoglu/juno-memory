import glob
import os
import subprocess
from collections.abc import Callable, Mapping

from juno.core import interp

HOMEBREW_GLOBS = (
    "/opt/homebrew/bin/python3*",
    "/usr/local/bin/python3*",
    "/home/linuxbrew/.linuxbrew/bin/python3*",
)


def run_text(cmd: list[str], timeout: float = 2.0) -> str:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except OSError, subprocess.SubprocessError:
        return ""
    return done.stdout if done.returncode == 0 else ""


def probe(path: str) -> interp.Version | None:
    if not os.access(path, os.X_OK):
        return None
    return interp.parse_version(
        run_text([path, "-I", "-S", "-c", "import sys; print(sys.version_info[:2])"])
    )


def homebrew() -> list[str]:
    found: list[str] = []
    for pattern in HOMEBREW_GLOBS:
        found += [p for p in sorted(glob.glob(pattern)) if interp.is_python_name(p)]
    return found


def pyenv() -> list[str]:
    root = run_text(["pyenv", "root"]).strip()
    if not root:
        return []
    pattern = os.path.join(root, "versions", "*", "bin", "python3")
    return sorted(glob.glob(pattern))


def uv() -> list[str]:
    return interp.parse_uv_list(run_text(["uv", "python", "list", "--only-installed"], 5.0))


def system(env: Mapping[str, str]) -> list[str]:
    found = ["/usr/bin/python3"]
    for directory in env.get("PATH", "").split(os.pathsep):
        if directory:
            found.append(os.path.join(directory, "python3"))
    return found


def gather(env: Mapping[str, str]) -> list[tuple[str, list[str]]]:
    producers: list[tuple[str, Callable[[], list[str]]]] = [
        ("homebrew", homebrew),
        ("pyenv", pyenv),
        ("uv", uv),
        ("system", lambda: system(env)),
    ]
    seen: set[str] = set()
    sources: list[tuple[str, list[str]]] = []
    for name, produce in producers:
        unique: list[str] = []
        for path in produce():
            if not os.path.exists(path):
                continue
            real = os.path.realpath(path)
            if real not in seen:
                seen.add(real)
                unique.append(path)
        sources.append((name, unique))
    return sources


def discover(
    env: Mapping[str, str],
    probe_fn: Callable[[str], interp.Version | None] = probe,
) -> tuple[str, interp.Version] | None:
    override = env.get("JUNO_PYTHON", "")
    if override:
        version = probe_fn(override)
        if version is not None and version >= interp.FLOOR:
            return override, version
        return None
    probed = [(name, [(p, probe_fn(p)) for p in paths]) for name, paths in gather(env)]
    return interp.pick(probed)
