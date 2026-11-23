"""Pure parts of `juno selftest`: question rows, path syntax and scoring of one search answer.

A question has an expected vault file (`expect`) and optionally an expected phrase (`sentence`).
A control has no `expect`: it passes when the search returns no result at all."""

import posixpath
from collections.abc import Mapping, Sequence
from typing import Any

from juno.core import eval as evalcore

MAX_QUESTION = 500
MAX_SENTENCE = 500
MAX_QUESTIONS = 200
DEFAULT_LIMIT = 5

Row = dict[str, Any]


class Invalid(ValueError):
    pass


def check_text(value: str | None, label: str, cap: int) -> str:
    text = " ".join((value or "").split())
    if not text:
        raise Invalid(f"{label} is empty")
    if len(text) > cap:
        raise Invalid(f"{label} is over {cap} characters")
    return text


def expect_syntax(raw: str, vault: str) -> str:
    """The vault-relative POSIX path of `raw`, or Invalid. Absolute paths must lie in the vault."""
    path = raw.strip()
    if not path or "\0" in path or "\\" in path:
        raise Invalid("--expect is empty or has an invalid character")
    if path.startswith("/"):
        root = posixpath.normpath(vault).rstrip("/") + "/"
        if not posixpath.normpath(path).startswith(root) or ".." in path.split("/"):
            raise Invalid("--expect is an absolute path outside the vault")
        path = posixpath.normpath(path)[len(root) :]
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise Invalid("--expect must be a plain vault-relative path (no '..', '.' or '//')")
    if not path.endswith(".md"):
        raise Invalid("--expect must be a Markdown file (.md)")
    return path


def new_row(qid: str, question: str, expect: str | None, sentence: str | None, when: str) -> Row:
    return {
        "id": qid,
        "question": question,
        "expect": expect,
        "sentence": sentence,
        "control": expect is None,
        "added": when,
    }


def squash(text: str) -> str:
    return " ".join(text.casefold().split())


def cut_by_budget(snippets: Sequence[str], budget: int | None) -> list[str]:
    """The snippets in rank order, one newline between them, cut at `budget` characters."""
    if budget is None:
        return list(snippets)
    left = max(budget, 0)
    out: list[str] = []
    for snip in snippets:
        out.append(snip[:left])
        left = max(left - len(snip) - 1, 0)
    return out


def score(row: Mapping[str, Any], hits: Sequence[Mapping[str, Any]], budget: int | None) -> Row:
    """One question against the ordered hits `juno search` returned (same limit)."""
    out: Row = {
        "id": row["id"],
        "question": row["question"],
        "expect": row["expect"],
        "control": bool(row["control"]),
        "returned": len(hits),
        "rank": None,
        "hit1": False,
        "hitk": False,
        "sentence": None,
        "passed": None,
    }
    if row["control"]:
        out["passed"] = not hits
        return out
    paths = [h["relpath"] for h in hits]
    if row["expect"] in paths:
        rank = paths.index(row["expect"]) + 1
        out.update(rank=rank, hit1=rank == 1, hitk=True)
    phrase = row.get("sentence")
    if phrase:
        out["sentence"] = sentence_state(row, hits, budget)
    return out


def sentence_state(
    row: Mapping[str, Any], hits: Sequence[Mapping[str, Any]], budget: int | None
) -> str:
    """`found` (in the budgeted text), `cut_by_budget` (in the full text, not in the budgeted
    one), `absent` (in no returned snippet of the file, or the file was not found)."""
    need = squash(row["sentence"])
    snippets = [h["snippet"] for h in hits]
    full = cut_by_budget(snippets, None)
    cut = cut_by_budget(snippets, budget)

    def present(texts: Sequence[str]) -> bool:
        return any(
            h["relpath"] == row["expect"] and need in squash(t)
            for h, t in zip(hits, texts, strict=True)
        )

    if present(cut):
        return "found"
    return "cut_by_budget" if present(full) else "absent"


def summarize(results: Sequence[Mapping[str, Any]]) -> Row:
    asked = [r for r in results if not r["control"]]
    controls = [r for r in results if r["control"]]
    sentences = [r["sentence"] for r in asked if r["sentence"]]
    hitk = sum(1 for r in asked if r["hitk"])
    n = len(asked)
    return {
        "questions": n,
        "hit_at_1": sum(1 for r in asked if r["hit1"]),
        "hit_at_k": hitk,
        "hit_at_1_rate": sum(1 for r in asked if r["hit1"]) / n if n else None,
        "hit_at_k_rate": hitk / n if n else None,
        "wilson_lower_hit_at_k": evalcore.wilson_lower(hitk, n) if n else None,
        "missed": n - hitk,
        "sentences": len(sentences),
        "sentence_found": sentences.count("found"),
        "sentence_cut_by_budget": sentences.count("cut_by_budget"),
        "sentence_absent": sentences.count("absent"),
        "controls": len(controls),
        "controls_passed": sum(1 for r in controls if r["passed"]),
    }
