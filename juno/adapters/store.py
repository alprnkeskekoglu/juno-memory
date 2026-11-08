import contextlib
import json
import os
import time
import unicodedata

from juno import config
from juno.adapters import fs

BACKUP_VERSIONS = 20


def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except FileNotFoundError, IsADirectoryError, NotADirectoryError:
        return ""


def write_text(path: str, text: str) -> None:
    fs.atomic_write(path, text.encode("utf-8"))


def read_json_dict(path: str) -> dict[str, object]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def norm(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def is_within(path: str, root: str) -> bool:
    a, b = norm(path), norm(root).rstrip(os.sep)
    return a == b or a.startswith(b + os.sep)


def rel_parts(path: str, root: str) -> list[str]:
    rest = path[len(root.rstrip(os.sep)) :].lstrip(os.sep)
    return [p for p in rest.split(os.sep) if p]


def identity(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError, ValueError:
        return None
    return st.st_dev, st.st_ino


def parts_under(path: str, root: str) -> list[str] | None:
    real = os.path.realpath(path)
    real_root = os.path.realpath(root)
    if is_within(real, real_root):
        depth = len(real_root.rstrip(os.sep).split(os.sep))
        return [p for p in unicodedata.normalize("NFC", real).split(os.sep)[depth:] if p]
    root_id = identity(real_root)
    if root_id is None:
        return None
    tail: list[str] = []
    current = real
    while True:
        if identity(current) == root_id:
            return [unicodedata.normalize("NFC", p) for p in reversed(tail)]
        parent, name = os.path.split(current)
        if parent == current or not name:
            return None
        tail.append(name)
        current = parent


def vault_rel(cfg: config.Config, path: str) -> list[str] | None:
    return parts_under(path, cfg.vault)


def vault_file(cfg: config.Config, *parts: str) -> str | None:
    clean = [p for part in parts for p in part.split("/") if p]
    if not clean or any(p in (".", "..") for p in clean):
        return None
    current = os.path.realpath(cfg.vault)
    for part in clean:
        current = os.path.join(current, part)
        if os.path.islink(current):
            return None
    found = vault_rel(cfg, current)
    same = found is not None and [norm(p) for p in found] == [norm(p) for p in clean]
    return current if same else None


def in_vault(cfg: config.Config, path: str) -> bool:
    return vault_rel(cfg, path) is not None


def in_state(cfg: config.Config, path: str) -> bool:
    return parts_under(path, cfg.state) is not None


def rel(cfg: config.Config, path: str) -> str:
    parts = vault_rel(cfg, path)
    return "/".join(parts) if parts is not None else path


def resolve(path: str, base: str | None) -> str:
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(base or os.getcwd(), expanded)
    return os.path.realpath(expanded)


def state_dir(cfg: config.Config, *parts: str) -> str:
    path = os.path.join(cfg.state, *parts)
    fs.ensure_private_dir(path)
    return path


def file_sig(path: str) -> list[int] | None:
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError, ValueError:
        return None
    return [st.st_size, st.st_mtime_ns]


def bump_counter(cfg: config.Config, name: str) -> None:
    with contextlib.suppress(Exception):
        path = os.path.join(state_dir(cfg), "counters.json")
        data = read_json_dict(path)
        data[name] = int(str(data.get(name) or 0)) + 1
        fs.atomic_write(path, (json.dumps(data, sort_keys=True) + "\n").encode())


def backup(cfg: config.Config, path: str) -> bool:
    if not os.path.isfile(path):
        return True
    parts = vault_rel(cfg, path) or [os.path.basename(path)]
    micros = time.time_ns() // 1000 % 10**6
    stamp = time.strftime("%H%M%S", time.localtime()) + f"-{micros:06d}"
    suffix = "-" + "%".join(parts)
    target = os.path.join(state_dir(cfg, "backups", time.strftime("%Y-%m-%d")), stamp + suffix)
    data = fs.read_bytes(path)
    if data is None:
        return True
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(target, 0o600)
    cap_backups(cfg, suffix)
    return True


def cap_backups(cfg: config.Config, suffix: str) -> None:
    root = state_dir(cfg, "backups")
    found: list[tuple[str, str, str]] = []
    for day in os.listdir(root):
        directory = os.path.join(root, day)
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if name.endswith(suffix) and len(name) == 13 + len(suffix):
                found.append((day, name, os.path.join(directory, name)))
    for _, _, old in sorted(found)[:-BACKUP_VERSIONS]:
        with contextlib.suppress(OSError):
            os.unlink(old)


def log_error(cfg: config.Config, event: str, exc: BaseException) -> None:
    with contextlib.suppress(Exception):
        tb = exc.__traceback__
        while tb and tb.tb_next:
            tb = tb.tb_next
        where = f"{os.path.basename(tb.tb_frame.f_code.co_filename)}:{tb.tb_lineno}" if tb else "-"
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        message = " ".join(str(exc).split())[:200]
        line = f"{stamp} {event or '-'} {type(exc).__name__} {where} {message!r}"
        fs.append_log(
            os.path.join(cfg.state, "hook-errors.log"),
            line,
            cfg.get_int("retention", "error_log_bytes"),
        )
