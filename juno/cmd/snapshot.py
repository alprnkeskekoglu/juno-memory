import argparse
import os
import re
import stat
import time
from collections.abc import Mapping
from typing import Any

from juno import config
from juno.adapters import git, ledgerio
from juno.cmd import write

SIGNING_RE = re.compile(r"gpg|sign|pinentry|secret key|ssh-keygen|agent", re.I)
GIT_FLAGS = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor="]


class Failed(Exception):
    pass


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-m", "--message", help="commit message (default: juno snapshot <date>)")
    parser.add_argument("--json", action="store_true")


def first_line(text: str) -> str:
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    return lines[0] if lines else "no output from git"


def vault_git(
    cfg: config.Config, vault: str, *args: str, timeout: float = 60.0
) -> tuple[int, str, str]:
    done = git.result(
        [*GIT_FLAGS, f"--git-dir={vault}/.git", f"--work-tree={vault}", *args],
        vault,
        timeout,
        cfg.env,
    )
    if done is None:
        raise Failed("git did not run")
    return done.returncode, done.stdout, done.stderr


def check_repo(cfg: config.Config, vault: str) -> None:
    try:
        info = os.lstat(os.path.join(vault, ".git"))
    except OSError:
        raise Failed("vault has no .git directory of its own") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise Failed("vault .git is a symlink or not a directory")
    code, out, _ = vault_git(cfg, vault, "rev-parse", "--absolute-git-dir")
    if code != 0 or os.path.realpath(out.strip()) != os.path.realpath(os.path.join(vault, ".git")):
        raise Failed("vault is not the root of its own git repository")


def take(cfg: config.Config, message: str | None) -> dict[str, Any]:
    vault = os.path.realpath(cfg.vault)
    check_repo(cfg, vault)
    code, _, err = vault_git(cfg, vault, "add", "-A", "--", ".")
    if code != 0:
        raise Failed(first_line(err))
    if not vault_git(cfg, vault, "status", "--porcelain")[1].strip():
        return {"status": "nothing to commit", "commit": None}
    text = message or f"juno snapshot {time.strftime('%Y-%m-%d')}"
    code, out, err = vault_git(cfg, vault, "commit", "-m", text)
    if code != 0:
        problem = first_line(err or out)
        if SIGNING_RE.search(err or out):
            raise Failed(f"commit signing failed ({problem})")
        raise Failed(problem)
    commit = vault_git(cfg, vault, "rev-parse", "HEAD")[1].strip()
    signature = vault_git(cfg, vault, "log", "-1", "--format=%G?")[1].strip()
    return {"status": "committed", "commit": commit, "message": text, "signature": signature}


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    def body(cfg: config.Config) -> int:
        seg = write.guard(cfg)
        try:
            with write.locked(cfg, write.VIEW_LOCK_SECONDS):
                result = take(cfg, args.message)
        except Failed as error:
            print(f"Juno: snapshot failed: {error}")
            return write.REFUSED
        ledgerio.record(cfg, seg, {"ev": "snapshot"})
        text = result["commit"] if result["status"] == "committed" else "nothing to commit"
        write.emit(args, result, text)
        return 0

    return write.execute(args, env, body)
