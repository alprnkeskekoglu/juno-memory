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
    turns,
)

KINDS = (rules, knowledge, threads, pitfalls, backlog, keys, procedures, selfmodel, turns, next)
KEYS = tuple(kind.KEY for kind in KINDS)
