"""One legacy vault, with every shape the bug reports have shown so far (made-up content)."""

import os

KEEP = "# Handoffs\n\n"
STUB_MARK = "auto: true"
COPIES = ("aaaa0001", "aaaa0002", "aaaa0003")
DASH_TWICE = "aaaa0004"
FOUR_REPOS = "aaaa0006"
MISFILED = "aaaa0007"
FOUR = ("r1", "r2", "r3", "r4")
BODYLESS = ("2026-11-01-empty.md", "2026-11-02-blank.md")
GOTCHAS_LINES = 120
HEAD = "---\ndate: 2026-11-01\nrepos: [alpha]\ntickets: []\nsource: 57fb7685\n---\n\n"


def card(seg: str, repos: str) -> str:
    return (
        f"## 2026-11-01 10:00 · - · work · {seg}\nRepos: {repos}\nTickets: -\n"
        "Did it.\nLearned: none\n\n"
    )


def write(vault: str, rel: str, text: str) -> None:
    path = os.path.join(vault, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def profile(name: str, gotchas: int = 0) -> str:
    lines = "".join(f"- gotcha number {i} of the {name} service\n" for i in range(gotchas))
    return f"# {name}\n\n## Gotchas\n{lines}" if gotchas else f"# {name}\n"


def build(vault: str) -> None:
    """Write the legacy shapes into `vault`: cards, notes and a profile."""
    write(vault, "projects/alpha.md", profile("alpha", GOTCHAS_LINES))
    write(vault, "projects/gamma.md", profile("gamma"))
    # cards of alpha in handoff files that are not repo files
    write(vault, "handoffs/alpha.md", KEEP + "".join(card(s, "alpha") for s in COPIES))
    write(vault, "handoffs/notes.md", KEEP + card(COPIES[0], "alpha") + card(COPIES[1], "alpha"))
    write(vault, "handoffs/ideas.md", KEEP + card(COPIES[1], "alpha") + card(COPIES[2], "alpha"))
    # a `Repos: -` card in two files
    write(vault, "handoffs/misc.md", KEEP + card(DASH_TWICE, "-"))
    write(vault, "handoffs/scratch.md", KEEP + card(DASH_TWICE, "-"))
    # a card filed once per named repo (4 repos)
    for repo in FOUR:
        write(vault, f"handoffs/{repo}.md", KEEP + card(FOUR_REPOS, ", ".join(FOUR)))
    # one single card in a repo file it does not name
    write(vault, "handoffs/gamma.md", KEEP + card(MISFILED, "delta"))
    # notes with a frontmatter and no body line
    write(vault, f"notes/{BODYLESS[0]}", HEAD)
    write(vault, f"notes/{BODYLESS[1]}", HEAD + "\n  \n")
    write(vault, "notes/2026-11-03-full.md", HEAD + "A real finding that stays\n")
