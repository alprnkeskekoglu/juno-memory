import os

from juno import config
from juno.adapters import store
from juno.core.detect import Tickets


def tickets_from(cfg: config.Config) -> Tickets:
    return Tickets(
        tuple(cfg.get_list("jira", "prefixes")),
        tuple(cfg.get_list("jira", "no_ticket_branches")),
    )


def excluded(cfg: config.Config) -> set[str]:
    return {store.norm(n.strip()) for n in cfg.get_list("repos", "exclude") if n.strip()}


def guarded(cfg: config.Config, real: str) -> bool:
    return store.is_within(real, cfg.state) or store.in_vault(cfg, real)


def existing_dir(path: str) -> str | None:
    current = path
    while current and not os.path.isdir(current):
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return current or None


def git_dirs(directory: str) -> tuple[str, str, str] | None:
    current = directory
    while True:
        dotgit = os.path.join(current, ".git")
        if os.path.isdir(dotgit):
            real = os.path.realpath(dotgit)
            return real, real, current
        if os.path.isfile(dotgit):
            text = store.read_text(dotgit).strip()
            if not text.startswith("gitdir:"):
                return None
            gitdir = os.path.realpath(os.path.join(current, text[len("gitdir:") :].strip()))
            common = store.read_text(os.path.join(gitdir, "commondir")).strip()
            common_dir = os.path.realpath(os.path.join(gitdir, common)) if common else gitdir
            return gitdir, common_dir, current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def has_git(path: str) -> bool:
    return os.path.lexists(os.path.join(path, ".git"))


def root_repo(cfg: config.Config, real: str, root: str) -> tuple[str, str] | None:
    parts = store.rel_parts(real, root)
    first = parts[0] if parts else None
    if not first or first.startswith(".") or not os.path.isdir(os.path.join(root, first)):
        return None
    name = tickets_from(cfg).split_dirname(first)[0]
    if store.norm(name) in excluded(cfg):
        return None
    path = os.path.join(root, first)
    return (name, path) if has_git(path) else None


def detect_repo(cfg: config.Config, path: str) -> tuple[str, str] | None:
    real = os.path.realpath(path)
    if guarded(cfg, real):
        return None
    roots = [os.path.realpath(r) for r in cfg.repo_roots()]
    for root in roots:
        if store.is_within(real, root):
            return root_repo(cfg, real, root)
    directory = existing_dir(real)
    dirs = git_dirs(directory) if directory else None
    if not dirs:
        return None
    _, common, top = dirs
    main = os.path.realpath(
        os.path.dirname(common) if os.path.basename(common) == ".git" else common
    )
    if guarded(cfg, main):
        return None
    for root in roots:
        if store.is_within(main, root):
            hit = root_repo(cfg, main, root)
            return (hit[0], top) if hit else None
    return None


def repo_dir(cfg: config.Config, name: str) -> str | None:
    if not name or name.startswith(".") or os.sep in name or store.norm(name) in excluded(cfg):
        return None
    for root in cfg.repo_roots():
        path = os.path.join(root, name)
        if os.path.isdir(path) and has_git(path) and not guarded(cfg, os.path.realpath(path)):
            return path
    return None


def plain_folder(cfg: config.Config, name: str) -> bool:
    if not name or name.startswith(".") or os.sep in name:
        return False
    return any(
        os.path.isdir(os.path.join(root, name)) and not has_git(os.path.join(root, name))
        for root in cfg.repo_roots()
    )


def current_branch(worktree: str) -> str | None:
    dirs = git_dirs(worktree)
    head = store.read_text(os.path.join(dirs[0], "HEAD")).strip() if dirs else ""
    return head[len("ref: refs/heads/") :] if head.startswith("ref: refs/heads/") else None


def repo_ticket(cfg: config.Config, worktree: str) -> str | None:
    tickets = tickets_from(cfg)
    ticket = tickets.branch_ticket(current_branch(worktree))
    return ticket or tickets.split_dirname(os.path.basename(worktree.rstrip(os.sep)))[1]


def repos_for(cfg: config.Config, paths: list[str]) -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    seen_dirs: set[str] = set()
    for path in paths:
        key = store.norm(os.path.dirname(path) if os.path.isfile(path) else path)
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        hit = detect_repo(cfg, path)
        if hit and hit[0] not in found:
            found[hit[0]] = repo_ticket(cfg, hit[1])
    return found


def is_overview_dir(cfg: config.Config, cwd: str) -> bool:
    real = store.norm(os.path.realpath(cwd))
    return any(store.norm(os.path.realpath(root)) == real for root in cfg.overview_roots())
