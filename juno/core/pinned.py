from juno.core import fold, inject, topics, vaultfmt
from juno.core.view import VaultView

PINNED_LABEL = "RULES (follow, pinned):"


def pinned_files(repos: list[str], fired: list[str]) -> list[str]:
    """Rule files whose pins are active: global, then touched repos, then fired topics."""
    files = ["rules/global.md"]
    files += [f"rules/{topics.REPO_DIR}/{fold.safe_name(r)}.md" for r in repos]
    files += [topics.topic_relpath(s) for s in sorted(fired) if topics.SLUG_RE.fullmatch(s)]
    return files


def render(texts: list[str]) -> str:
    """The pinned block before trimming: the label line, then one neutralized line per rule."""
    if not texts:
        return ""
    return "\n".join([PINNED_LABEL, *("- " + inject.neutralize(t) for t in texts)])


def pinned_block(view: VaultView, repos: list[str], fired: list[str], limit: int) -> str:
    texts: list[str] = []
    for rel in pinned_files(repos, fired):
        text = view.read(rel)
        if not text or view.hidden(rel, text):
            continue
        texts += vaultfmt.pinned_rule_texts(text)
    block = render(texts)
    return inject.trim(block, limit, "the vault") if block else ""
