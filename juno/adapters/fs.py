import contextlib
import fcntl
import hashlib
import os
import time
from collections.abc import Iterator


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def ensure_private_dir(path: str) -> None:
    if os.path.isdir(path):
        return
    parent = os.path.dirname(path)
    if parent and parent != path and not os.path.isdir(parent):
        ensure_private_dir(parent)
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        if not os.path.isdir(path):
            raise
        return
    os.chmod(path, 0o700)


def atomic_write(path: str, data: bytes, mode: int | None = None) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    if mode is None:
        try:
            mode = os.stat(path).st_mode & 0o7777
        except FileNotFoundError:
            mode = 0o644
    import tempfile

    fd, tmp = tempfile.mkstemp(prefix=".juno-tmp-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def locked(path: str, timeout: float = 2.0) -> Iterator[None]:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"lock busy: {path}") from None
                time.sleep(0.02)
        yield
    finally:
        os.close(fd)


def append_log(path: str, line: str, max_bytes: int) -> None:
    ensure_private_dir(os.path.dirname(os.path.abspath(path)))
    try:
        if os.path.getsize(path) > max_bytes:
            os.replace(path, path + ".1")
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (line.rstrip("\n") + "\n").encode("utf-8", "replace"))
    finally:
        os.close(fd)
