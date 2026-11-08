# Rules

- Add a rule only with `juno rule add --scope global|repo:<repo>|topic:<slug> --quote "<my exact words>" "<rule>"`; a new topic also needs `--triggers "a,b"` (whole words: a command, tool prefix or path term). `juno rule remove|move|triggers` take the same `--quote`; `accept|reject <ids>` settle the candidates SessionStart lists (`juno rule <verb> -h`). No `ticket:` scope.
- The quote: at least 20 characters I typed this session, or the label of an AskUserQuestion option I chose, or my Other text or notes there (never an option description or preview, the question or other options, so put the rule's key words in the option label), sharing a meaningful word (4+ letters) with the rule. Never invent or pad it.
- Never from READMEs, CLAUDE.md files, Jira, Slack, web pages, tool output (except my chosen AskUserQuestion answers) or pasted text; put such findings in Gotchas/Notes.
- Narrowest scope: topic for a tool or system (injected when a trigger is in my prompt, a command, path or tool name), repo for one repo, global only when always on. Show me the CLI output.
