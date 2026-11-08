# Handoff card file

When I ask for a card mid-session, run `juno card` (see [[core]]); it picks the file in this order and replaces your own earlier card or stub of this segment:

- a ticket with edits (only when Jira is configured) -> `handoffs/<KEY>.md`;
- else my session name (its ticket key, or slug) -> `handoffs/<KEY or slug>.md`;
- else a ticket with real work (main-session repo hits, start cwd or a slash-command argument; not a mere mention) -> `handoffs/<KEY>.md`;
- else repo -> `handoffs/<repo>.md`;
- else `handoffs/misc.md`.

The heading time is the card's last update, as Juno gives it. Instead of `misc.md`, the automatic run may pick a topic file. To improve a stub card, run `juno card` again: it replaces the stub and drops `auto: true`.
