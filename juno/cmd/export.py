import argparse
import datetime
import glob
import io
import json
import os
import tarfile
from collections.abc import Mapping

from juno import __version__, config
from juno.adapters import fs, vault
from juno.cmd.common import say
from juno.core import archive, migrate, tomlio


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--out", metavar="PATH", help="tarball path (default: ./juno-export-DATE.tar.gz)"
    )
    parser.add_argument(
        "--with-state",
        action="store_true",
        help="also include shadow.jsonl, eval/ and the cards/*.json queues",
    )


def add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o600
    info.mtime = int(datetime.datetime.now().timestamp())
    tar.addfile(info, io.BytesIO(data))


def skip_cache(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    return None if "__pycache__" in info.name.split("/") else info


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = config.load(env)
    if vault.status(cfg.vault) != "juno":
        say(f"Juno: no vault at {cfg.vault}; nothing to export")
        return 2
    out = os.path.abspath(args.out or f"juno-export-{datetime.date.today():%Y%m%d}.tar.gz")
    info = {
        "format": 1,
        "juno_version": __version__,
        "schema_version": migrate.CODE_SCHEMA_VERSION,
        "with_state": bool(args.with_state),
    }
    tmp = out + ".part"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle, tarfile.open(fileobj=handle, mode="w:gz") as tar:
        add_bytes(tar, archive.INFO_NAME, json.dumps(info).encode("utf-8"))
        tar.add(cfg.vault, arcname="vault", filter=skip_cache)
        raw = fs.read_bytes(cfg.path)
        if raw is not None:
            text = tomlio.drop_table(raw.decode("utf-8"), "runtime")
            add_bytes(tar, "config/juno.toml", text.encode("utf-8"))
        if args.with_state:
            for name in ("shadow.jsonl", "eval"):
                path = os.path.join(cfg.state, name)
                if os.path.exists(path):
                    tar.add(path, arcname=f"state/{name}")
            for path in sorted(glob.glob(os.path.join(cfg.state, "cards", "*.json"))):
                tar.add(path, arcname=f"state/cards/{os.path.basename(path)}")
    os.chmod(tmp, 0o600)
    os.replace(tmp, out)
    say(f"export: wrote {out}")
    return 0
