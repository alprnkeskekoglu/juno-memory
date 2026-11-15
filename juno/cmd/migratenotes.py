import os

from juno import config
from juno.adapters import store
from juno.adapters.vaultview import DiskVault
from juno.cmd import write
from juno.cmd.common import say
from juno.core import notebody, writegate

ARCHIVE = "archive/decayed/forgotten-{month}.md"


def archive_bodyless(cfg: config.Config, dry_run: bool) -> int:
    """Archive each notes/*.md file that has a frontmatter and no body line."""
    view = DiskVault(cfg)
    found = {
        f"notes/{n}": text
        for n in view.names("notes")
        if notebody.bodyless(text := view.read(f"notes/{n}"))
    }
    if not found:
        say("notes: no body-less notes")
        return 0
    day = write.today()
    archive = ARCHIVE.format(month=day[:7])
    path = os.path.join(cfg.vault, *archive.split("/"))
    for rel, text in found.items():
        if dry_run:
            say(f"notes: would archive {rel}")
            continue
        note = os.path.join(cfg.vault, *rel.split("/"))
        old = store.read_text(path) or "# Forgotten lines\n\n"
        line = notebody.archive_line("(no body line)", "archived", day, rel, None, text)
        store.backup(cfg, path)
        store.write_text(path, writegate.join([*old.splitlines(), line]))
        store.backup(cfg, note)
        os.unlink(note)
        say(f"notes: archived {rel} to {archive} (backup under {cfg.state}/backups)")
    return 0
