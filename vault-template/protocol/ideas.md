# Protocol: Ideas

Part of [[core]].

Create an idea file when we start working on the idea, or when I explicitly ask to save an idea (then `status: planned`). Other brainstormed or deferred ideas get no file; if they matter, keep them on the handoff card's next-steps line.

- File: `ideas/<slug>.md`. Create it with `juno idea --slug <slug> [--tags a,b] "<one line>"` (status `planned`); afterwards `juno idea --slug <slug> --status in-progress|done|dropped [--note "..."] [--outcome "..."]`.
- Frontmatter: `status` (planned | in-progress | done | dropped), `created`, `updated`, `tags`, `source` (segment_id[:8]). Sections: `## Idea` (one line first), `## Why`, `## Notes`, `## Links`, `## Outcome`.
- On a state change, run `juno idea` with `--status`. On done or dropped, add `--outcome`: where it shipped (commit, PR, ticket) or why it was dropped.
- Never store customer data or secrets in an idea.

Links: [[core]], [[links]]
