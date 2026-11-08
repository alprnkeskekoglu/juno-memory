import contextlib
import os
import sqlite3
import stat
import time
from collections.abc import Iterator
from typing import Any

from juno import config
from juno.adapters import fs, keysio, vaultview
from juno.core import keys as keycore
from juno.core import retire, vaultfmt
from juno.core import search as core

SCHEMA_VERSION = 3
SQLITE_HEADER = b"SQLite format 3\x00"
MAX_READ_BYTES = 1 << 20
LOCK_SECONDS = 10.0
INDEXED_SKIP_KINDS = frozenset({"generated", "config"})
REBUILD_CODES = (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)

SCHEMA = """
CREATE TABLE files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    relpath TEXT NOT NULL UNIQUE,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    inode INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    private INTEGER NOT NULL DEFAULT 0,
    archive INTEGER NOT NULL DEFAULT 0,
    retired INTEGER NOT NULL DEFAULT 0,
    retired_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE file_tickets (file_id INTEGER NOT NULL, ticket TEXT NOT NULL);
CREATE INDEX file_tickets_idx ON file_tickets (ticket, file_id);
CREATE TABLE file_repos (file_id INTEGER NOT NULL, repo TEXT NOT NULL);
CREATE INDEX file_repos_idx ON file_repos (repo, file_id);
CREATE TABLE file_supersedes (file_id INTEGER NOT NULL, raw TEXT NOT NULL, kind TEXT NOT NULL);
CREATE INDEX file_supersedes_idx ON file_supersedes (file_id);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE VIRTUAL TABLE passages USING fts5(
    title, heading, body, keys, raw UNINDEXED, h UNINDEXED, day UNINDEXED, sup UNINDEXED,
    tokenize='porter unicode61 remove_diacritics 2'
);
"""


class SchemaMismatchError(Exception):
    pass


def is_corrupt(exc: sqlite3.DatabaseError) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    return code is not None and (code & 0xFF) in REBUILD_CODES


def remove_db(db: str) -> None:
    try:
        st: os.stat_result | None = os.lstat(db)
    except FileNotFoundError:
        st = None
    if st is not None:
        if not stat.S_ISREG(st.st_mode):
            raise RuntimeError(f"refusing to replace non-regular file: {db}")
        with open(db, "rb") as handle:
            head = handle.read(len(SQLITE_HEADER))
        if st.st_size == 0 or head == SQLITE_HEADER:
            os.unlink(db)
        else:
            os.replace(db, db + ".bad")
    for suffix in ("-wal", "-shm", "-journal"):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(db + suffix)


def create_schema(db: str) -> None:
    tmp = f"{db}.new.{os.getpid()}"
    try:
        conn = sqlite3.connect(tmp, timeout=5, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        finally:
            conn.close()
        os.chmod(tmp, 0o600)
        os.replace(tmp, db)
    finally:
        for suffix in ("", "-wal", "-shm", "-journal"):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp + suffix)


def check_index(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if version != SCHEMA_VERSION:
        if tables and not (version and {"files", "passages"} <= tables):
            raise RuntimeError("refusing to replace a SQLite database that is not a Juno index")
        raise SchemaMismatchError(f"schema version {version}")
    try:
        for table in ("files", "passages", "meta"):
            conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    except sqlite3.OperationalError as error:
        if "no such table" in str(error) or "no such column" in str(error):
            raise SchemaMismatchError(str(error)) from error
        raise


def connect_build(db: str) -> sqlite3.Connection:
    if not os.path.exists(db):
        create_schema(db)
    for attempt in (0, 1):
        conn = None
        try:
            conn = sqlite3.connect(db, timeout=5, isolation_level=None)
            check_index(conn)
            conn.execute("PRAGMA synchronous=NORMAL")
            return conn
        except RuntimeError:
            if conn is not None:
                conn.close()
            raise
        except SchemaMismatchError:
            if conn is not None:
                conn.close()
            if attempt:
                raise
            remove_db(db)
            create_schema(db)
        except sqlite3.DatabaseError as error:
            if conn is not None:
                conn.close()
            if attempt or not is_corrupt(error):
                raise
            remove_db(db)
            create_schema(db)
    raise RuntimeError("unreachable")


def open_readonly(db: str) -> sqlite3.Connection:
    if not os.path.exists(db):
        raise FileNotFoundError(f"index not found: {db} (run a sync first)")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError:
        conn.close()
        raise
    if version != SCHEMA_VERSION:
        conn.close()
        raise RuntimeError("index schema is outdated or empty; run a sync to rebuild it")
    return conn


Walked = tuple[str, str, tuple[int, int, int], int]


def walk(vault: str) -> Iterator[Walked]:
    stack = [("", vault)]
    while stack:
        prefix, directory = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if name.startswith("."):
                continue
            try:
                if entry.is_symlink():
                    continue
                rel = f"{prefix}{name}"
                if entry.is_dir(follow_symlinks=False):
                    stack.append((rel + "/", entry.path))
                elif name.endswith(".md") and entry.is_file(follow_symlinks=False):
                    if vaultfmt.kind_of(rel) in INDEXED_SKIP_KINDS:
                        continue
                    st = entry.stat(follow_symlinks=False)
                    yield rel, entry.path, (st.st_size, st.st_mtime_ns, st.st_ino), st.st_dev
            except OSError:
                continue


def read_file(path: str, dev: int, ino: int) -> tuple[str, tuple[int, int, int]] | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags | getattr(os, "O_CLOEXEC", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_ino != ino or st.st_dev != dev:
            return None
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(MAX_READ_BYTES)
        return data.decode("utf-8", errors="replace"), (st.st_size, st.st_mtime_ns, st.st_ino)
    finally:
        os.close(fd)


def is_archive_path(rel: str) -> bool:
    return any(part.casefold() == "archive" for part in rel.split("/")[:-1])


def index_file(
    conn: sqlite3.Connection,
    file_id: int,
    rel: str,
    text: str,
    preset: str,
    keymap: dict[str, list[str]],
    mtime_ns: int,
) -> None:
    fields, body = vaultfmt.parse_frontmatter(text)
    passages = core.split_passages(body)
    stem = rel.rsplit("/", 1)[-1][:-3]
    title = core.title_of(rel, fields, body)
    tickets, repos = core.extract_meta(rel, fields, body)
    conn.execute(
        "UPDATE files SET title=?, status=?, private=?, archive=? WHERE id=?",
        (
            title,
            vaultfmt.field_str(fields, "status"),
            int(vaultfmt.is_private(fields)),
            int(is_archive_path(rel)),
            file_id,
        ),
    )
    conn.executemany("INSERT INTO file_tickets VALUES (?, ?)", [(file_id, t) for t in tickets])
    conn.executemany("INSERT INTO file_repos VALUES (?, ?)", [(file_id, r.lower()) for r in repos])
    conn.executemany(
        "INSERT INTO file_supersedes VALUES (?, ?, ?)",
        [(file_id, raw, kind) for raw, kind in retire.supersedes_refs(fields)],
    )
    fts_title = core.fold_text(f"{title} {stem}", preset)
    fallback = core.file_day(rel, fields, mtime_ns)
    base = file_id * core.STRIDE
    conn.executemany(
        "INSERT INTO passages(rowid, title, heading, body, keys, raw, h, day, sup) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                base + i,
                fts_title,
                core.fold_text(h, preset),
                core.fold_text(t, preset),
                key_field(keymap, keycore.passage_hash(t), preset),
                t,
                keycore.passage_hash(t),
                core.passage_day(h, t, fallback),
                "1" if core.is_superseded(rel, h) else "",
            )
            for i, (h, t) in enumerate(passages or [("", "")])
        ],
    )


def key_field(keymap: dict[str, list[str]], h: str, preset: str) -> str:
    return core.fold_text(keycore.key_text(keymap.get(h, [])), preset)


def refresh_keys(conn: sqlite3.Connection, keymap: dict[str, list[str]], preset: str) -> None:
    updates = [
        (want, rowid)
        for rowid, h, have in conn.execute("SELECT rowid, h, keys FROM passages")
        if (want := key_field(keymap, h, preset)) != have
    ]
    conn.executemany("UPDATE passages SET keys=? WHERE rowid=?", updates)


def clear_file(conn: sqlite3.Connection, file_id: int) -> None:
    base = file_id * core.STRIDE
    conn.execute("DELETE FROM passages WHERE rowid >= ? AND rowid < ?", (base, base + core.STRIDE))
    for table in ("file_tickets", "file_repos", "file_supersedes"):
        conn.execute(f"DELETE FROM {table} WHERE file_id=?", (file_id,))


def resolution_of(conn: sqlite3.Connection) -> retire.Resolution:
    supersedes: dict[int, list[tuple[str, str]]] = {}
    for fid, raw, kind in conn.execute(
        "SELECT file_id, raw, kind FROM file_supersedes ORDER BY file_id, rowid"
    ):
        supersedes.setdefault(fid, []).append((raw, kind))
    metas = {
        rel: retire.FileMeta(status, tuple(supersedes.get(fid, ())))
        for fid, rel, status in conn.execute(
            "SELECT id, relpath, status FROM files WHERE archive = 0"
        )
    }
    return retire.resolve(metas)


def apply_retirement(conn: sqlite3.Connection) -> retire.Resolution:
    resolution = resolution_of(conn)
    conn.execute("UPDATE files SET retired=0, retired_by=''")
    conn.executemany(
        "UPDATE files SET retired=1, retired_by=? WHERE relpath=?",
        [(why, rel) for rel, why in resolution.retired.items()],
    )
    return resolution


def build_locked(
    vault: str, db: str, t0: float, preset: str, keys_file: str | None = None
) -> dict[str, Any]:
    conn = connect_build(db)
    keys_sig = keysio.signature(keys_file) if keys_file else "none"
    keymap = keysio.load(keys_file) if keys_file else {}
    scanned = reindexed = deleted = errors = 0
    changed = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='preset'").fetchone()
            if (row[0] if row else "none") != preset:
                conn.execute("DELETE FROM files")
                conn.execute("DELETE FROM passages")
                for table in ("file_tickets", "file_repos", "file_supersedes"):
                    conn.execute(f"DELETE FROM {table}")
                conn.execute("INSERT OR REPLACE INTO meta VALUES ('preset', ?)", (preset,))
                changed = True
            known = {
                rel: (fid, (size, mtime, ino))
                for fid, rel, size, mtime, ino in conn.execute(
                    "SELECT id, relpath, size, mtime_ns, inode FROM files"
                )
            }
            seen: set[str] = set()
            for rel, path, sig, dev in walk(vault):
                scanned += 1
                seen.add(rel)
                prev = known.get(rel)
                if prev and prev[1] == sig:
                    continue
                try:
                    got = read_file(path, dev, sig[2])
                except OSError:
                    errors += 1
                    if prev:
                        clear_file(conn, prev[0])
                        changed = True
                    continue
                if got is None:
                    errors += 1
                    continue
                text, sig = got
                if prev:
                    file_id = prev[0]
                    clear_file(conn, file_id)
                    conn.execute(
                        "UPDATE files SET size=?, mtime_ns=?, inode=? WHERE id=?", (*sig, file_id)
                    )
                else:
                    cursor = conn.execute(
                        "INSERT INTO files(relpath, size, mtime_ns, inode) VALUES (?, ?, ?, ?)",
                        (rel, *sig),
                    )
                    assert cursor.lastrowid is not None
                    file_id = cursor.lastrowid
                try:
                    conn.execute("SAVEPOINT f")
                    index_file(conn, file_id, rel, text, preset, keymap, sig[1])
                    conn.execute("RELEASE f")
                    reindexed += 1
                except Exception:
                    conn.execute("ROLLBACK TO f")
                    conn.execute("RELEASE f")
                    errors += 1
            for rel, (file_id, _) in known.items():
                if rel not in seen:
                    clear_file(conn, file_id)
                    conn.execute("DELETE FROM files WHERE id=?", (file_id,))
                    deleted += 1
            if reindexed or deleted or changed:
                apply_retirement(conn)
            row = conn.execute("SELECT value FROM meta WHERE key='keys_sig'").fetchone()
            if (row[0] if row else "none") != keys_sig:
                refresh_keys(conn, keymap, preset)
                conn.execute("INSERT OR REPLACE INTO meta VALUES ('keys_sig', ?)", (keys_sig,))
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return {
        "scanned": scanned,
        "reindexed": reindexed,
        "deleted": deleted,
        "errors": errors,
        "ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def prepare_db_dir(db: str, vault: str) -> None:
    from juno.adapters import store

    if store.is_within(os.path.realpath(db), os.path.realpath(vault)):
        raise ValueError(f"index path must be outside the vault: {db}")
    fs.ensure_private_dir(os.path.dirname(os.path.abspath(db)))


def build_index(
    vault: str,
    db: str,
    state_dir: str | None = None,
    preset: str = "none",
    keys_file: str | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    if not os.path.isdir(vault):
        raise NotADirectoryError(f"vault not found: {vault}")
    prepare_db_dir(db, vault)
    old_umask = os.umask(0o077)
    try:
        with fs.locked(db + ".lock", LOCK_SECONDS):
            result = build_locked(vault, db, t0, preset, keys_file)
    finally:
        os.umask(old_umask)
    if state_dir is not None:
        write_retired(db, state_dir)
    return result


def write_retired(db: str, state_dir: str) -> None:
    conn = open_readonly(db)
    try:
        resolution = resolution_of(conn)
    finally:
        conn.close()
    fs.ensure_private_dir(state_dir)
    vaultview.write_retired(state_dir, resolution)


def read_resolution(db: str) -> retire.Resolution:
    conn = open_readonly(db)
    try:
        return resolution_of(conn)
    finally:
        conn.close()


def retired_paths(db: str) -> dict[str, str]:
    conn = open_readonly(db)
    try:
        return dict(conn.execute("SELECT relpath, retired_by FROM files WHERE retired=1"))
    finally:
        conn.close()


def db_path(cfg: config.Config) -> str:
    return os.path.join(cfg.state, "index.sqlite3")


def sync(cfg: config.Config) -> tuple[str, dict[str, Any]]:
    fs.ensure_private_dir(cfg.state)
    db = db_path(cfg)
    preset = cfg.get_str("language", "preset")
    return db, build_index(cfg.vault, db, cfg.state, preset, keysio.keys_path(cfg.state))
