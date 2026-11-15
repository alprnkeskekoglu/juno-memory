"""Read-only git facts for `juno consolidate`: tracked paths and deleted paths at a pinned sha."""

from juno.adapters import git, stalefix

GIT_SECONDS = 30.0
MARK = "\x01"


def tracked(pin: stalefix.Pin) -> list[str] | None:
    done = git.result(
        ["-c", "core.quotepath=off", "ls-tree", "-r", "--name-only", pin.sha], pin.dir, GIT_SECONDS
    )
    if done is None or done.returncode != 0:
        return None
    return done.stdout.splitlines()


def deleted(pin: stalefix.Pin) -> dict[str, str] | None:
    """Every path a commit in the history of pin.sha deleted -> short sha of the latest deletion."""
    done = git.result(
        [
            "-c",
            "core.quotepath=off",
            "log",
            pin.sha,
            "--diff-filter=D",
            "--name-only",
            f"--format={MARK}%h",
        ],
        pin.dir,
        GIT_SECONDS,
    )
    if done is None or done.returncode != 0:
        return None
    found: dict[str, str] = {}
    sha = ""
    for line in done.stdout.splitlines():
        if line.startswith(MARK):
            sha = line[1:]
        elif line:
            found.setdefault(line, sha)
    return found
