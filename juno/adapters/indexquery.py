import sqlite3
from typing import Any

from juno.adapters import index
from juno.core import search as core

OVERFETCH_MAX = 200
MATCH = "passages MATCH ?"
NOT_STUB = "passages.heading NOT LIKE ?"
NO_KEYS = "{title heading body} : "


def has_match(conn: sqlite3.Connection, term: str, where: list[str], params: list[Any]) -> bool:
    sql = (
        "SELECT 1 FROM passages JOIN files f ON f.id = passages.rowid / "
        f"{core.STRIDE} WHERE {' AND '.join(where)} LIMIT 1"
    )
    return conn.execute(sql, [f'"{term}"', *params]).fetchone() is not None


def close_matches(
    conn: sqlite3.Connection, token: str, where: list[str], params: list[Any], preset: str
) -> list[str]:
    word = core.fold_same_length(token.lower(), preset)
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS temp.vocab USING fts5vocab(main, passages, 'row')"
    )
    terms = [
        r[0]
        for r in conn.execute(
            "SELECT term FROM temp.vocab WHERE length(term) BETWEEN ? AND ?",
            (len(word) - 1, len(word) + 1),
        )
    ]
    return [w for w in core.close_words(word, terms) if has_match(conn, w, where, params)]


def corrections_for(
    conn: sqlite3.Connection, tokens: list[str], where: list[str], params: list[Any], preset: str
) -> dict[str, list[str]]:
    fixes: dict[str, list[str]] = {}
    for tok in dict.fromkeys(tokens):
        if (
            len(tok) >= core.CORRECT_MIN_LEN
            and tok.isalpha()
            and not has_match(conn, tok, where, params)
        ):
            words = close_matches(conn, tok, where, params, preset)
            if words:
                fixes[tok] = words
    return fixes


def day_filters(since: str | None, until: str | None, table: str) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if since:
        clauses.append(f"{table}.day >= ?")
        params.append(since)
    if until:
        clauses.append(f"{table}.day <= ?")
        params.append(until)
    return clauses, params


def ticket_hits(
    conn: sqlite3.Connection,
    wanted: list[str],
    where: list[str],
    params: list[Any],
    preset: str,
    days: tuple[str | None, str | None] = (None, None),
) -> list[dict[str, Any]]:
    dclauses, dparams = day_filters(*days, "p")
    sql = (
        "SELECT f.relpath, p.heading, p.raw, p.day, p.sup FROM files f JOIN passages p "
        f"ON p.rowid = f.id * {core.STRIDE} WHERE f.relpath = ? "
        f"AND {' AND '.join([*where, *dclauses])}"
    )
    out: list[dict[str, Any]] = []
    for rel in wanted:
        row = conn.execute(sql, [rel, *params, *dparams]).fetchone()
        if row:
            meta = (row[3], bool(row[4]))
            found = core.hit(row[0], row[1], row[2], [], core.TICKET_SCORE, preset, meta)
            found["key"] = core.dup_key(*row[:3])
            out.append(found)
    return out


def filters_for(repo: str | None, ticket: str | None, archive: bool) -> tuple[list[str], list[Any]]:
    filters = ["f.retired = 0", "f.private = 0"]
    params: list[Any] = []
    if not archive:
        filters.append("f.archive = 0")
    if repo:
        filters.append("EXISTS (SELECT 1 FROM file_repos r WHERE r.file_id = f.id AND r.repo = ?)")
        params.append(str(repo).lower())
    if ticket:
        filters.append(
            "EXISTS (SELECT 1 FROM file_tickets t WHERE t.file_id = f.id AND t.ticket = ?)"
        )
        params.append(str(ticket).upper())
    return filters, params


def search(
    db: str,
    query: object,
    repo: str | None = None,
    ticket: str | None = None,
    limit: int = 5,
    corrections: dict[str, list[str]] | None = None,
    spread: bool = False,
    archive: bool = False,
    preset: str = "none",
    ticket_files: bool = True,
    keys_weight: float = core.KEYS_WEIGHT,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    tokens = core.query_tokens(query, preset)
    wanted = core.query_ticket_files(query) if ticket_files else []
    if not isinstance(limit, int) or limit <= 0 or not (tokens or wanted):
        return []
    limit = min(limit, 100)
    conn = index.open_readonly(db)
    try:
        filters, fparams = filters_for(repo, ticket, archive)
        dclauses, dparams = day_filters(since, until, "passages")
        where = [MATCH, *filters[:2], NOT_STUB, *filters[2:], *dclauses]
        params: list[Any] = [core.STUB_LIKE, *fparams, *dparams]
        out = ticket_hits(conn, wanted, filters, fparams, preset, (since, until))
        seen = {h["relpath"] for h in out}
        ticket_keys = [h.pop("key") for h in out]
        if not tokens or len(out) >= limit:
            return out[:limit]
        sql = (
            "SELECT f.relpath, passages.heading, passages.raw, "
            f"bm25(passages, {', '.join(map(str, core.bm25_weights(keys_weight)))}) AS s, "
            "passages.day, passages.sup "
            f"FROM passages JOIN files f ON f.id = passages.rowid / {core.STRIDE} "
            f"WHERE {' AND '.join(where)} ORDER BY s LIMIT ?"
        )
        fixes = (
            corrections_for(conn, tokens, where, params, preset) if corrections is not None else {}
        )
        if corrections is not None:
            corrections.update(fixes)
        shown = tokens + [w for ws in fixes.values() for w in ws]
        rows: list[Any] = []
        for match in core.match_modes(tokens, fixes):
            query_match = match if keys_weight > 0 else f"{NO_KEYS}({match})"
            rows = conn.execute(
                sql, [query_match, *params, min(limit * 10, OVERFETCH_MAX)]
            ).fetchall()
            if rows:
                break
        cands = core.dedupe(rows, seen, ticket_keys)
        found = core.pick(out, cands, limit, -(-limit // 2) if spread else limit, shown, preset)
        flagged = (
            {r[0] for r in conn.execute("SELECT relpath FROM files WHERE archive = 1")}
            if archive
            else set()
        )
        for item in found:
            if item["relpath"] in flagged:
                item["archive"] = True
        return found
    finally:
        conn.close()
