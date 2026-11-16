from juno.adapters.learnkinds import (
    backlog,
    keys,
    knowledge,
    next,
    pitfalls,
    procedures,
    rules,
    selfmodel,
    threads,
    ticket,
    turns,
)

KINDS = (
    rules,
    knowledge,
    threads,
    pitfalls,
    backlog,
    keys,
    procedures,
    ticket,
    selfmodel,
    turns,
    next,
)
KEYS = tuple(kind.KEY for kind in KINDS)
