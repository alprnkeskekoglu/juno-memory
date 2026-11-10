# Handoff card file

When I ask for a card mid-session, run `juno card` (see [[core]]); it picks the file in this order and replaces your own earlier card or stub of this segment:

- a ticket with edits (only when Jira is configured) -> `handoffs/<KEY>.md`;
- else my session name (its ticket key, or slug) -> `handoffs/<KEY or slug>.md`;
- else a ticket with real work (main-session repo hits, start cwd or a slash-command argument; not a mere mention) -> `handoffs/<KEY>.md`;
- else repo -> `handoffs/<repo>.md`;
- else `handoffs/misc.md`.

`--tickets K1,K2` and `--repos r1,r2` name the card's tickets and repos explicitly: a ticket must have been seen in this segment (or be linked in `--links`), a repo touched in it or have a profile; otherwise Juno refuses.

The heading time is the card's last update, as Juno gives it. Instead of `misc.md`, the automatic run may pick a topic file. To improve a stub card, run `juno card` again: it replaces the stub and drops `auto: true`.

One card per segment per file. When the doctor warns about older cards:

- a stub whose card run is over: `juno card --segment <seg8> --label L --learned "..." --evidence "<your words>" "<done>" "<next>"` writes its real card (every stub of that segment, in all files, in one write), or `juno card dismiss <seg8> --evidence E` archives the stubs with their excerpt under `archive/handoffs/`;
- two cards of one segment in a file: `juno card merge <file> <seg8> --evidence E` keeps the first heading, joins the bodies and the `Learned:` lines (with "; ") and archives the other card.

All three are one undoable write; each refuses a segment that has a real card (or, for the stubs, a card job still pending).
