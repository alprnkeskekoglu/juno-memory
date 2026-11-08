import os
import re
from typing import Any

from juno.core import fold, prompts
from juno.core.redact import redact

WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
ASK_TOOL = "AskUserQuestion"
BOUNDARY_RE = re.compile(r"\bgit\b(?:\s+-[Cc]\s+\S+)*\s+(?:commit|push)\b|\bgh\s+pr\s+create\b")
PR_URL_RE = re.compile(r"github\.com/[^/\s]+/[^/\s]+/pull/\d+")


def shell_tokens(command: str) -> list[str]:
    import shlex

    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return [t for t in re.split(r"[\s;&|<>()`]+", command) if t]


def expand_home(token: str, home: str) -> str:
    return os.path.expanduser(token.replace("${HOME}", home).replace("$HOME", home))


def bash_path_candidates(tokens: list[str], home: str) -> list[tuple[str, bool]]:
    found: list[tuple[str, bool]] = []
    for i, token in enumerate(tokens):
        if token == "-C" and i > 0 and tokens[i - 1] == "git" and i + 1 < len(tokens):
            target = expand_home(tokens[i + 1], home)
            if "$" not in target:
                found.append((target, True))
        elif token.startswith(("/", "~", "$HOME", "${HOME}")):
            target = expand_home(token, home)
            if "$" not in target:
                found.append((target, False))
    return found


def is_boundary(command: str, stdout: str) -> bool:
    return bool(BOUNDARY_RE.search(command)) or PR_URL_RE.search(stdout) is not None


def write_target(tool_in: dict[str, Any]) -> str | None:
    path = tool_in.get("file_path") or tool_in.get("notebook_path")
    return path if isinstance(path, str) and path else None


def asked_questions(tool_in: dict[str, Any]) -> list[dict[str, Any]]:
    questions = tool_in.get("questions")
    return [q for q in questions if isinstance(q, dict)] if isinstance(questions, list) else []


def option_list(question: dict[str, Any]) -> list[str]:
    options = question.get("options")
    labels: list[str] = []
    for option in options if isinstance(options, list) else []:
        label = option.get("label") if isinstance(option, dict) else None
        labels.append(fold.nfc(label) if isinstance(label, str) else "")
    return labels


def option_labels(question: dict[str, Any]) -> set[str]:
    return {label for label in option_list(question) if label}


def split_selection(answer: str, options: set[str]) -> list[str]:
    tokens = answer.split(", ")
    picked: list[str] = []
    other: list[str] = []
    i = 0
    while i < len(tokens):
        end = next(
            (j for j in range(len(tokens), i, -1) if ", ".join(tokens[i:j]) in options), None
        )
        if end is None:
            other.append(tokens[i])
            i += 1
            continue
        if other:
            picked.append(", ".join(other))
            other = []
        picked.append(", ".join(tokens[i:end]))
        i = end
    return picked + ([", ".join(other)] if other else [])


def answer_texts(question: dict[str, Any], answer: object) -> list[str]:
    options = option_labels(question)
    if isinstance(answer, list):
        picked = [fold.nfc(a) for a in answer if isinstance(a, str)]
    elif isinstance(answer, str):
        answer = fold.nfc(answer)
        picked = (
            split_selection(answer, options) if question.get("multiSelect") is True else [answer]
        )
    else:
        return []
    return picked


def chosen_texts(tool_in: dict[str, Any], response: object) -> list[str]:
    data = response if isinstance(response, dict) else {}
    raw_answers, raw_notes = data.get("answers"), data.get("annotations")
    answers: dict[str, Any] = raw_answers if isinstance(raw_answers, dict) else {}
    notes: dict[str, Any] = raw_notes if isinstance(raw_notes, dict) else {}
    texts: list[Any] = []
    for question in asked_questions(tool_in):
        name = question.get("question")
        if not isinstance(name, str):
            continue
        texts.extend(answer_texts(question, answers.get(name)))
        note = notes.get(name)
        texts.append(note.get("notes") if isinstance(note, dict) else None)
    cleaned = (redact(prompts.strip_pastes(fold.nfc(t))) for t in texts if isinstance(t, str))
    return [t for t in cleaned if t.strip()]


def ask_rows(tool_in: dict[str, Any], response: object) -> list[dict[str, Any]]:
    data = response if isinstance(response, dict) else {}
    raw = data.get("answers")
    answers: dict[str, Any] = raw if isinstance(raw, dict) else {}
    rows: list[dict[str, Any]] = []
    for q, question in enumerate(asked_questions(tool_in)):
        name = question.get("question")
        if not isinstance(name, str) or name not in answers:
            continue
        options = option_list(question)
        picked_texts = answer_texts(question, answers[name])
        picked = sorted({options.index(t) for t in picked_texts if t in options})
        rows.append(
            {
                "q": q,
                "n_options": len(options),
                "rec": next((i for i, o in enumerate(options) if "(Recommended)" in o), -1),
                "picked": picked,
                "other": any(t not in options for t in picked_texts),
                "multi": question.get("multiSelect") is True,
            }
        )
    return rows
