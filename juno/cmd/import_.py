import argparse
import os
import tarfile
from collections.abc import Mapping

from juno import config
from juno.adapters import fs
from juno.cmd import common, init
from juno.cmd.common import say
from juno.core import archive


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", metavar="PATH", help="tarball made by juno export")
    common.add_install_options(parser)


def kind_of(member: tarfile.TarInfo) -> str:
    if member.isreg():
        return "file"
    if member.isdir():
        return "dir"
    if member.issym():
        return "symlink"
    return "other"


def validate(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = tar.getmembers()
    for member in members:
        error = archive.member_error(member.name, kind_of(member), member.linkname)
        if error:
            raise ValueError(error)
    if not any(m.name == archive.INFO_NAME for m in members):
        raise ValueError("not a Juno export (juno-export.json missing)")
    return members


def extract(tar: tarfile.TarFile, member: tarfile.TarInfo, target: str, replace: bool) -> None:
    if os.path.lexists(target) and not replace:
        return
    if member.isdir():
        os.makedirs(target, exist_ok=True)
        os.chmod(target, (member.mode & 0o777) | 0o700)
    elif member.issym():
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.symlink(member.linkname, target)
    else:
        source = tar.extractfile(member)
        assert source is not None
        os.makedirs(os.path.dirname(target), exist_ok=True)
        fs.atomic_write(target, source.read(), member.mode & 0o777)


def occupied(cfg: config.Config) -> bool:
    return os.path.lexists(cfg.vault) and (
        not os.path.isdir(cfg.vault) or bool(os.listdir(cfg.vault))
    )


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = config.load(env)
    try:
        with tarfile.open(args.path, "r:*") as tar:
            members = validate(tar)
            if occupied(cfg):
                say(f"Juno: {cfg.vault} exists and is not empty; refusing to import")
                return 2
            conf = [m for m in members if m.name == "config/juno.toml"]
            if conf and not os.path.exists(cfg.path):
                extract(tar, conf[0], cfg.path, False)
                os.chmod(cfg.path, 0o600)
                say(f"config: wrote {cfg.path}")
            elif conf:
                say(f"config: {cfg.path} exists; left unchanged")
            cfg = config.load(env)
            if occupied(cfg):
                say(f"Juno: {cfg.vault} exists and is not empty; refusing to import")
                return 2
            for member in members:
                top, _, rest = member.name.partition("/")
                if top == "vault":
                    extract(tar, member, os.path.join(cfg.vault, rest) if rest else cfg.vault, True)
                elif top == "state" and rest:
                    extract(tar, member, os.path.join(cfg.state, rest), False)
    except (OSError, tarfile.TarError, ValueError) as error:
        say(f"Juno: import failed: {error}")
        return 2
    say(f"import: vault restored at {cfg.vault}")
    return init.run(argparse.Namespace(dry_run=False, **vars_of(args)), env)


def vars_of(args: argparse.Namespace) -> dict[str, str | None]:
    return {
        "claude_settings": getattr(args, "claude_settings", None),
        "bin_dir": getattr(args, "bin_dir", None),
    }
