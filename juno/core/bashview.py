import re
import shlex

HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
OPERATORS = frozenset({";", "&&", "||", "|", "&", "(", ")", "<", ">", ">>", "<<", "<<<"})
WRAPPERS = frozenset({"sudo", "env", "time", "command", "nohup", "exec", "xargs", "builtin"})
PROSE_FLAGS = frozenset(
    {
        "--body",
        "--body-file",
        "--message",
        "--notes",
        "--notes-file",
        "--title",
        "--description",
        "--comment",
        "--text",
        "--search",
        "--query",
    }
)
MESSAGE_EXES = frozenset({"git", "gh", "hg", "jj"})
# The memory CLI's own verbs: the verb stays, its positional arguments are data (a search query,
# a rule or note text), not intent.
MEMORY_EXES = frozenset({"juno"})
GREP_EXES = frozenset({"grep", "egrep", "fgrep", "rg", "ag", "ack", "zgrep"})
GREP_VALUE_FLAGS = frozenset(
    {"-A", "-B", "-C", "-m", "-f", "-g", "-t", "--include", "--exclude", "--glob", "--type"}
)
GREP_PATTERN_FLAGS = frozenset({"-e", "--regexp"})
# A grep pattern that is one identifier, path or flag stays; a regex or word list is dropped.
LITERAL_RE = re.compile(r"^[\w./:-]+$")
# A word with a slash or a file extension names a file; it is matched by path segment.
PATH_LIKE_RE = re.compile(r"/|\.[A-Za-z][A-Za-z0-9]{0,5}$")


def strip_heredocs(command: str) -> str:
    lines: list[str] = []
    ending: str | None = None
    for line in command.splitlines():
        if ending is not None:
            if line.strip() == ending:
                ending = None
            continue
        found = HEREDOC_RE.search(line)
        if found:
            ending = found.group(2)
            line = HEREDOC_RE.sub(" ", line)
        lines.append(line)
    return " ; ".join(lines)


def command_segments(command: str) -> list[list[str]]:
    """Tokens per pipeline segment, without heredoc bodies."""
    text = strip_heredocs(command)
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        tokens = text.split()
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in OPERATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [seg for seg in segments if seg]


def segment_exe(tokens: list[str]) -> int | None:
    for i, token in enumerate(tokens):
        if "=" in token and not token.startswith("-") and token.split("=", 1)[0].isidentifier():
            continue
        if token in WRAPPERS or token.startswith("-"):
            continue
        return i
    return None


def own_tokens(tokens: list[str], exe: str) -> list[str]:
    """Drop the text a command carries rather than runs: prose flags, messages, grep patterns."""
    if exe in MEMORY_EXES:
        return [t for t in tokens if not t.startswith("-")][:1] + [
            t for t in tokens if t.startswith("-")
        ]
    kept: list[str] = []
    skip = False
    pattern_seen = False
    grep = exe in GREP_EXES
    explicit = grep and any(t in GREP_PATTERN_FLAGS or t.startswith("--regexp=") for t in tokens)
    for token in tokens:
        if skip:
            skip = False
            continue
        flag, eq, _ = token.partition("=")
        if flag in PROSE_FLAGS or flag == "--grep" or (grep and flag == "--regexp"):
            skip = not eq
            continue
        if exe in MESSAGE_EXES and token in ("-m", "-F"):
            skip = True
            continue
        if grep:
            if token in GREP_PATTERN_FLAGS:
                skip = True
                continue
            if token in GREP_VALUE_FLAGS:
                kept.append(token)
                skip = True
                continue
            if not token.startswith("-") and not explicit and not pattern_seen:
                pattern_seen = True
                if not LITERAL_RE.match(token):
                    continue
        kept.append(token)
    return kept


def is_path_like(token: str) -> bool:
    return (
        not token.startswith("-")
        and "=" not in token
        and not any(ch.isspace() for ch in token)
        and PATH_LIKE_RE.search(token) is not None
    )


def bash_parts(command: str) -> tuple[str, frozenset[str], tuple[str, ...]]:
    """The command's own words, its executables (first word of each pipeline segment) and the
    path-like words it names, which are matched by whole path segment, not as text."""
    parts: list[str] = []
    execs: set[str] = set()
    paths: list[str] = []
    for tokens in command_segments(command):
        at = segment_exe(tokens)
        exe = tokens[at].rsplit("/", 1)[-1].lower() if at is not None else ""
        if exe:
            execs.add(exe)
        head = tokens[: (at or 0) + 1]
        words = head + own_tokens(tokens[len(head) :], exe)
        paths += [w for w in words if is_path_like(w)]
        parts.append(" ".join(w for w in words if not is_path_like(w)))
    return " ; ".join(parts), frozenset(execs), tuple(paths)


def bash_view(command: str) -> tuple[str, frozenset[str]]:
    """The command's own words (paths aside) and its executables."""
    text, execs, _ = bash_parts(command)
    return text, execs
