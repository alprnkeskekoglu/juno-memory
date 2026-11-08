import argparse
import contextlib
import json
import re
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from juno import config
from juno.adapters import checks, fs, ledgerio, queues
from juno.core import fold, privacy
from juno.core.errors import Refused as Refused
from juno.core.ledger import Segment

LOCK_SECONDS = 2.0
VIEW_LOCK_SECONDS = 5.0
REFUSED = 2


def clean_line(cfg: config.Config, text: str | None, label: str) -> str:
    return privacy.clean_line(text, label, cfg.get_list("jira", "prefixes"))


def lock_path(cfg: config.Config) -> str:
    return f"{cfg.state}/vault.lock"


@contextlib.contextmanager
def locked(cfg: config.Config, seconds: float = LOCK_SECONDS) -> Iterator[None]:
    try:
        with fs.locked(lock_path(cfg), seconds):
            yield
    except TimeoutError:
        raise Refused("the vault is busy (vault.lock held); retry in a moment") from None


def guard(cfg: config.Config) -> Segment:
    reason = checks.write_refusal(cfg)
    if reason:
        raise Refused(reason)
    return ledgerio.fold(cfg, cfg.env["CLAUDE_CODE_SESSION_ID"])


def preset(cfg: config.Config) -> str:
    return cfg.get_str("language", "preset")


def find_prompt(cfg: config.Config, seg: Segment, quote: str) -> str:
    folded = fold.fold(quote, preset(cfg))
    for pid, text in seg.quote_sources():
        if folded and folded in fold.fold(text, preset(cfg)):
            return pid
    raise Refused(
        "quote is not a verbatim part of any prompt typed or AskUserQuestion answer chosen "
        "in this segment"
    )


def match_quote(cfg: config.Config, seg: Segment, quote: str, rule: str) -> str:
    problem = fold.quote_problem(
        quote,
        rule,
        preset(cfg),
        cfg.language_list("stopwords"),
        cfg.get_int("caps", "quote_min_chars"),
    )
    if problem:
        raise Refused(problem)
    return find_prompt(cfg, seg, quote)


def chosen(cfg: config.Config, seg: Segment, cid: str) -> bool:
    word = re.compile(r"(?<![a-z0-9])" + re.escape(cid) + r"(?![a-z0-9])")
    return any(word.search(fold.fold(text, preset(cfg))) for _, text in seg.quote_sources())


def require_chosen(cfg: config.Config, seg: Segment, items: list[queues.Item]) -> None:
    unchosen = [str(i["id"]) for i in items if not chosen(cfg, seg, str(i["id"]))]
    if unchosen:
        raise Refused(
            f"the user did not choose {', '.join(unchosen)} in this segment: each id must appear "
            "in a prompt the user typed or an AskUserQuestion answer the user chose"
        )


def today() -> str:
    return time.strftime("%Y-%m-%d")


def emit(args: argparse.Namespace, data: Any, text: str) -> None:
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(text)


def execute(
    args: argparse.Namespace,
    env: Mapping[str, str],
    body: Callable[[config.Config], int],
) -> int:
    cfg = config.load(env)
    try:
        return body(cfg)
    except (Refused, queues.QueueError) as error:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(error)}))
        else:
            print(checks.refusal_line(str(error)))
        return REFUSED
