import os

from juno import config
from juno.adapters import cardrun, ledgerio, store


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "run":
        return 2
    sid = argv[1]
    cfg = config.load()
    if os.environ.get("JUNO_DISABLE") != "1" or ledgerio.ledger_path(cfg, sid) is None:
        return 1
    try:
        return cardrun.run(cfg, sid, os.environ.get("JUNO_CARD_TOKEN", ""))
    except Exception as exc:
        store.log_error(cfg, "card-run", exc)
        return 1
