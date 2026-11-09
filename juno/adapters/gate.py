import os
import time
from dataclasses import dataclass

from juno import config
from juno.adapters import fs, ledgerio, store, writelog
from juno.core import fold, privacy, writegate
from juno.core.errors import Refused
from juno.core.ledger import Segment


@dataclass(frozen=True)
class Result:
    id: str | None
    rel: str
    changed: bool


def prefixes(cfg: config.Config) -> list[str]:
    return cfg.get_list("jira", "prefixes")


def scan_text(cfg: config.Config, text: str, label: str) -> str:
    value = "\n".join(" ".join(line.split()) for line in fold.nfc(text).strip().splitlines())
    if not value:
        raise Refused(f"{label} is empty")
    return privacy.check(value, label, prefixes(cfg))


def scan_line(cfg: config.Config, text: str, label: str) -> str:
    value = privacy.clean_line(text, label, prefixes(cfg))
    writegate.check_line(value, label)
    return value


def anchor(
    cfg: config.Config, seg: Segment, evidence: str | None, user_only: bool, label: str = "evidence"
) -> str:
    text = scan_evidence(cfg, evidence, label)
    preset = cfg.get_str("language", "preset")
    folded = fold.fold(text, preset)
    if len(text) >= cfg.get_int("caps", "quote_min_chars"):
        for pid, source in seg.quote_sources():
            if folded in fold.fold(source, preset):
                return f"user {pid}"
    if user_only:
        raise Refused(
            f"{label} must be words you typed or chose in this segment "
            f"(at least {cfg.get_int('caps', 'quote_min_chars')} characters, verbatim)"
        )
    kind, _, name = text.partition(":")
    if kind == "repo" and name in seg.repos:
        return f"repo {name}"
    if kind == "ticket" and name.upper() in seg.tickets:
        return f"ticket {name.upper()}"
    raise Refused(
        f"{label} is not anchored in this session: quote words typed or chosen in this "
        "segment, or give repo:<name> or ticket:<KEY> that the session touched"
    )


def scan_evidence(cfg: config.Config, text: str | None, label: str) -> str:
    value = " ".join(fold.nfc(text).split())
    if not value:
        raise Refused(f"{label} is required")
    return privacy.check(value, label, prefixes(cfg))


def vault_file(cfg: config.Config, rel: str) -> str:
    path = store.vault_file(cfg, rel)
    if path is None:
        raise Refused(f"{rel} is not a plain vault path (outside the vault or through a symlink)")
    return path


def read(cfg: config.Config, rel: str) -> str:
    return store.read_text(vault_file(cfg, rel))


def run_limit(cfg: config.Config, seg: Segment) -> int:
    return cfg.get_int("caps", "run_max") + seg.cap_raised


def check_run_cap(cfg: config.Config, seg: Segment) -> None:
    limit = run_limit(cfg, seg)
    if writelog.live_count(writelog.read(cfg), seg.sid) >= limit:
        raise Refused(
            f"this session already made {limit} vault writes (the cap is {limit}); it can be "
            'raised only with `juno cap raise --by N --evidence "<user\'s words>"` after the '
            "user approves, or by [caps] run_max in juno.toml"
        )


def commit(
    cfg: config.Config,
    seg: Segment,
    verb: str,
    rel: str,
    new_text: str,
    evidence: str,
    card: bool = False,
    extra: dict[str, object] | None = None,
    delete: bool = False,
    engine: bool = False,
    run_cap: bool = True,
) -> Result:
    path = vault_file(cfg, rel)
    existed = os.path.isfile(path)
    old = store.read_text(path)
    if new_text == old and not delete:
        return Result(None, rel, False)
    if run_cap and not engine and not (extra and "of" in extra):
        check_run_cap(cfg, seg)
    store.backup(cfg, path)
    if delete:
        os.unlink(path)
    else:
        fs.atomic_write(path, new_text.encode("utf-8"))
    wid = writelog.new_id()
    writelog.append(
        cfg,
        {
            "id": wid,
            "sid": seg.sid,
            "seg": seg.sid[:8],
            "t": round(time.time(), 3),
            "verb": verb,
            "file": rel,
            "created": not existed,
            "evidence": evidence,
            "after": fs.sha256(new_text.encode("utf-8")),
            "hunks": writegate.encode(writegate.hunks(old, new_text)),
            **({"engine": True} if engine else {}),
            **(extra or {}),
        },
    )
    rec: dict[str, object] = {"ev": "vault_write", "id": wid, "verb": verb, "file": rel}
    if card:
        rec["card"] = rel
    ledgerio.record(cfg, seg, rec)
    return Result(wid, rel, True)
