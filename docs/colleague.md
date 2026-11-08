# Using Juno as a colleague

Juno is one person's memory. A colleague runs their own install and keeps their own vault.

- Install the plugin and run `juno init`. It creates an empty vault from the template, so nothing of anyone else's work is inside.
- Company data, tickets, partner names and customer details stay in the vault of the person who worked with them. Never copy a vault between people: it holds the owner's decisions, corrections and private notes, and `juno import` is for moving your own vault to another machine of yours.
- To share something, write it down on purpose (a README, a ticket, a rule you both agree on) and let the other person's Claude read it from there. Their Juno then stores what it learns in their own vault.
- Settings are per user. `juno init` merges a small, recorded set of entries into your Claude settings, and `juno uninstall` removes exactly those and leaves the vault alone.
- Sessions run by scripts or in `-p` mode never read or write the vault. Only an interactive session does.
