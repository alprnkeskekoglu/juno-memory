# Juno v2 spec

Juno is a portable second brain for Claude Code. It replaces Saga v1. This spec is the v1 spec minus the removed parts.

How to read the references:
- `[v1: <section>]` points to a section of the Saga v1 plan (not part of this repository). `[v1 backlog: <section>]` points to the Saga v1 backlog design. Port the detail from there. Where this spec differs, this spec wins.
- `[plan]`, `[phase0]` and `[phase6]` point to the author's private design and phase notes, which are not part of this repository. They are kept as provenance only.
- "Decided here" marks a choice the inputs did not settle. Each one gives a one-line reason, and all of them are listed again in section 17.

## 1. Scope and non-goals

### In scope
- One user, one vault per machine. macOS is the primary platform and Linux is supported (XDG paths). [plan]
- Claude Code is the only client. Juno ships as a plugin (hooks, skills, agents), a `juno` CLI shim, and a reversible settings merge. [plan]
- Phase 2 parity with v1 for every feature on the plan's Carry list (section 9). After that, the migration of the v1 install (Phase 5), then live improvements one at a time (Phase 6). [plan]

### Non-goals (removed from v1)
| Removed | v1 source | Note |
|---|---|---|
| Alarms (sweep, import, `alarms/`, skill, agent, launchd job, schemas, fixtures) | [v1: Layout], `docs/alarms-design.md` | The data moves to `~/saga-v1-archive/` with mode 0700. It is never injected or indexed. [plan] |
| Mentees (`mentees/`, reader agent, launchd job, skill) | `docs/mentees-design.md` | Same as alarms. [plan] |
| Company tool names | [v1: Ticket file and repo profile], protocol/slack-digest.md | The engine never names a company tool. Skill text names a Jira tool only generically. [plan, Phase 2 done-when] |
| Grants, the Bash guard, denylists, the `guards` switch, `[known_gaps]` | [v1: Vault write guard], [v1: Rules policy] Grant | Only cheap CLI checks are kept (section 8.1). The accepted gaps are in section 8.3. [plan] |
| Network Jira | [v1: Ticket] | Jira is optional and configured. Ticket data comes from the session's own Jira tool. [plan] |
| Company names, channel names, absolute user paths in code | everywhere | They move to config or examples only. [plan] |
| Dashboard alarm and mentee panels, the dashboard launchd job | [v1: Dashboard] | The dashboard is built on demand (section 12.10). |

Network exceptions. There are exactly four, and all are stated in the code: the session-end run calls `claude -p --tools ""`, `juno keys backfill` calls the same `claude -p` flags in the foreground under a `--max-usd` cap (12.1), the thread scan may call `gh pr view` (read-only, capped), and `juno stale --fix` runs `git fetch` in the user's repos and calls `claude -p` with read-only file tools in a throwaway export (12.6.1). Nothing else uses the network. [plan]

## 2. Principles (carried, adjusted)

Carried from [v1: Principles] with these changes:
- Markdown is the source of truth. Index, ledgers, caches and eval live in the state dir, never in the vault, and can be rebuilt. A busy or locked index is never deleted.
- Only the live interactive main session writes memory. There are two background exceptions: the session-end card run (section 11) and, only when the user turns it on, the weekly stale fixer (12.6.1). Both write only through engine code.
- Rules change only with a verbatim quote of the user's own words (section 10.4).
- Every hook output is at most 9,500 chars. Claude Code's cap is 10,000 [phase0 0.1]. The output has visible truncation markers and keeps `RULES (follow)` apart from `CONTEXT (data, not instructions)`.
- Runtime is the Python 3.14+ standard library only. Hooks run as `<python> -I -S <entry>`.
- Fail open for Claude's work: a broken hook never blocks a tool call or a prompt. From Phase 4, fail closed for vault writes.
- The user makes as few decisions as possible. The user approves only rules, the cutover and pushes. [plan]
- The self model is named "Model". It models the user and never adopts the user's identity. [plan, Phase 6]

## 3. Repository layout

The repo is `~/juno` on the user's personal GitHub, MIT licence. [plan] The repo root is the plugin root.

```
juno/                          repo root = plugin root (${CLAUDE_PLUGIN_ROOT})
  .claude-plugin/
    plugin.json                plugin manifest (name juno, version, author, licence)
    marketplace.json           local marketplace entry for this repo
  hooks/hooks.json             plugin hooks (section 6)
  skills/juno/SKILL.md         /juno lookup skill (v1 /saga)
  skills/backlog/SKILL.md      /backlog skill
  skills/research/SKILL.md     /juno:research skill (6.9)
  skills/voice-import/SKILL.md /juno:voice-import skill (12.13, #38)
  agents/researcher.md         web research agent (WebSearch, WebFetch), used by the research skill
  agents/verifier.md           one-claim verifier (WebFetch only)
  output-styles/juno-*.md     the Juno character as three plugin output styles, one per humour dose (13.10)
  bin/juno                     POSIX sh entry: interpreter lookup, then juno/cli.py
  bin/juno-hook                POSIX sh hook launcher (section 6.1)
  juno/                        Python package
    core/                      pure logic: fold, detect, packs, trim, cards, learn, search,
                               links, stale, topics, threads, backlog, rules, eval, shadow, meta
    adapters/                  thin I/O: fs.py (atomic write, lock, backup), git.py, clock.py,
                               env.py, claude.py (the claude -p call), gh.py, jira.py (Phase 3)
    hooks/                     one entry script per event (session_start.py, subagent_start.py,
                               user_prompt_submit.py, pre_tool_use.py, post_tool_use.py,
                               post_tool_use_failure.py, stop.py, session_end.py) and card_run.py
    cli.py                     argparse dispatch; each verb imports its module lazily
    config.py                  load juno.toml over code defaults
    paths.py                   vault, state, config, plugin root, interpreter cache
  vault-template/              core.md, protocol/, rules/global.md (empty), folder skeleton,
                               juno-brain.toml
  ui/                          UI mod (v1 saga-ui, renamed)
  tests/                       run.py, support/, fixtures/, test_*.py
  docs/spec.md                 this file
  docs/colleague.md            a colleague's data stays in their own vault [plan]
  pyproject.toml               ruff, mypy, test-selection config (section 15)
```

- **Decided here: the repo root is the plugin root, and `hooks/hooks.json`, `skills/` and `agents/` sit at the root rather than inside `.claude-plugin/`.** Reason: these are Claude Code's default component paths ([phase0 0.1] cites `hooks/hooks.json`), so no manifest path overrides are needed. This places the files differently from the plan's tree, but the contents are the same.
- Hook entries live in `juno/hooks/`, one file per event, so each event's import set can be tested on its own (section 6.3).
- The package is flat, with no src layout. [plan]

## 4. Vault

### 4.1 Location and layout

The vault is `~/juno-vault`. It is local only and has its own git repo for snapshots. It is never inside the code repo, so no `git add` can push memory. [plan]

```
~/juno-vault/
  juno-brain.toml            schema_version = 2
  core.md                    memory protocol, <= core_chars; invariant sections marked
  protocol/                  task procedures (handoffs, rules, vault-files, links, ideas, backlog)
  rules/                     global.md, repos/<repo>.md, topics/<slug>.md; changed only with the user's quote;
                             legacy rules/<KEY>.md read-only
  self/                      values.md, decisions.md, communication.md, tensions.md   (Phase 6)
    answers/YYYY-MM.md       raw AskUserQuestion log                                  (Phase 6)
  decisions/<topic>.md       current decision of a topic with no owner file (a ticket or repo keeps its decisions in `tasks/<KEY>.md` or `projects/<repo>.md` `## Decisions`)
  knowledge/<slug>.md        durable truths (sources, valid_from)
  procedures/<slug>.md       step sequences that worked                               (Phase 6)
  projects/<repo>.md         repo profiles
  tasks/<KEY>.md             only when [jira] is configured
  handoffs/<name>.md         cards per ticket, repo or topic
  notes/YYYY-MM-DD-<slug>.md situational facts
  inbox.md                   juno capture items not yet routed                        (Phase 4)
  threads.md, pitfalls.md
  backlog/<slug>.md, ideas/<slug>.md
  archive/                   nothing is deleted; searched only with --archive
    handoffs/, decisions/, decayed/
  _generated/                engine output: learned.md, reports/YYYY-WW.md
```

[plan, Vault layout]. The state dir holds the index, ledgers, eval, A/B and prediction logs, and usage traces. None of these go in the vault.

- `core.md` marks its invariant sections with `<!-- juno:invariant -->` ... `<!-- /juno:invariant -->`. The invariant sections are privacy, data-not-instructions and rules policy. **Decided here: this marker syntax.** Reason: the plan requires marked sections but gives no syntax, and HTML comments render invisibly in Obsidian.
- **Decided here: the Phase 6 folders (`self/`, `procedures/`) and `inbox.md` are created empty by the template.** Reason: the layout stays stable across phases, and empty folders cost nothing.

### 4.2 File formats (carried)

| File | Format | Source |
|---|---|---|
| Handoff card | the v1 heading `## YYYY-MM-DD HH:MM · <KEY or -> · <label> · <seg8>`, then `Repos:`, `Tickets:`, 2-5 lines, `Links:` (each entry `[[target]] (reason)`, or `-` when none has a reason), `**Decision:**`, `Learned:` | [v1: Handoff card], v1 core.md "Handoff cards" |
| Handoff file name | v1 routing steps 1-5. Steps 1-3 (ticket) apply only with `[jira]`. Step 4 (repo, no ticket or name) no longer copies the card into every touched repo's file: the card is written once per repo that is in both its own `Repos:` line and the segment's touched repos; when that intersection is empty, once to the first repo its `Repos:` line names, else to the segment's first touched repo (`routing.repo_targets`). Before the card exists (reminder, stub, prompt) the target is that first touched repo. | [v1: Handoff file name] |
| Ticket file, repo profile | v1 frontmatter and sections. The generated block markers become `<!-- juno:tickets:start -->` / `<!-- juno:tickets:end -->`. | [v1: Ticket file and repo profile] |
| Rule line | `- YYYY-MM-DD · <rule> · src: user <pid8> · "<quote>"` (migrated v1 lines may also read `src: unquoted`; an accepted native-memory import candidate reads `src: import:memory/<slug>/<file>.md`, 13.11) | [v1: Rules policy] Write |
| Threads line | `- [<tag>] <text> · since YYYY-MM-DD` | [v1: Layout] |
| Pitfall line | `- <text> · triggers: <t1>, <t2> · <evidence>; segment <seg8> · YYYY-MM-DD` | [v1: Layout] |
| Backlog item | v1 frontmatter, the four sections, and a Log | [v1 backlog: 3. Vault layout and file format] |
| Idea | `status`, `created`, `updated`, `tags`, `source`, plus the sections | v1 protocol/ideas.md |
| `decisions/<topic>.md` | only for a topic with no owner file: frontmatter `topic`, `updated`, `valid_from`. One `## Current` section with the decision lines (`- <decision> (<source>, YYYY-MM-DD)`), and `## History`. A ticket or repo keeps the same lines in the `## Decisions` section of `tasks/<KEY>.md` or `projects/<repo>.md` (first `##` section of a task file, before the first `##` section of a profile); superseded lines go to `archive/decisions/<topic>.md` (#31) | decided here (below) |
| `juno-brain.toml` | `schema_version = 6`, `created = YYYY-MM-DD` | [plan]; schema 2 carries the stale fixer exception in the `core.md` Privacy block, schema 3 the aligned quote rule and cap sentence, schema 4 the ticket decisions in `decisions/<KEY>.md`, schema 5 the reasoned `Links:` examples in the `core.md` card format, schema 6 the decisions in their owner's file (13.8) |

- **Decided here: the `decisions/<topic>.md` format.** Reason: the plan replaces `decisions.md` with per-topic files but gives no format. Keeping the current and superseded lines in separate sections lets the packs inject only `## Current`. Since #31 a ticket or repo with an owner file keeps its lines in the owner's `## Decisions`; the topic file format is unchanged for ownerless topics.
- Retirement, `supersedes:`, `status: archived` and `visibility: private` behave as in v1 [v1: Phase 2 item 2]. The protected folders are `rules`, `tasks`, `handoffs`, `projects`, `decisions` and `self`. **Decided here: `decisions` and `self` are added to the protected list.** Reason: they hold the current truth and must not be retired by a stray `supersedes:`.

### 4.3 v1 to v2 mapping

History: removed by decision 61.

## 5. Config and state

### 5.1 Config file

The path is `~/.config/juno/juno.toml`. When `XDG_CONFIG_HOME` is set, the path is `$XDG_CONFIG_HOME/juno/juno.toml`. `JUNO_CONFIG` overrides both (tests use it). Code defaults apply to every missing key, and any non-matching type falls back to the default with a doctor WARN. Keys come from v1 `saga.toml` and `sagacore.DEFAULTS`, minus the removed features. Every v1 constant that names a user, path, prefix, model, budget or language moves here. [plan]

| Key | Default | v1 origin / note |
|---|---|---|
| `[runtime] python` | discovered at init | `saga_cards.PYTHON` hardcoded `/opt/homebrew/bin/python3` (section 13.2) |
| `[runtime] plugin_root` | set at init, refreshed at SessionStart | new, for the shim (section 13.3) |
| `[paths] vault` | `~/juno-vault` | `SAGA_VAULT`; env `JUNO_VAULT` overrides |
| `[paths] state` | platform default (5.2) | `SAGA_STATE`; env `JUNO_STATE` overrides |
| `[paths] claude_dir` | `~/.claude` | `SAGA_CLAUDE_DIR`; transcripts under `projects/` |
| `[language] preset` | `"none"` | `"tr"` turns on Turkish folding and cues, and the suffix strip of trigger matching and trigger candidates (10.2, 10.3) [plan] |
| `[language] correction_cues` | preset list | `sagacore.CUES` |
| `[language] user_cues`, `self_cues` | preset lists | `saga_cards.USER_CUES`, `SELF_CUES` (Learning moments) |
| `[language] stopwords` | preset list | `sagacore.STOPWORDS` (quote overlap) |
| `[jira] base_url` | `""` | Phase 3 [plan]. Only builds the default `url` of `juno task set` (`<base_url>/browse/<KEY>`); the engine never calls it |
| `[jira] prefixes` | `[]` (empty = no ticket detection) | `[tickets] prefixes` |
| `[privacy] names` | `[]` | Names (any case) that must not appear in a verbatim example of `self/voice.md` (12.13), on top of the names Juno already knows. Not a scan: no other write path uses it (#34) |
| `[privacy] allow_prefixes` | `[]` | Extra `PREFIX-digits` keys (for example test case keys `TC-123456`) the privacy scan treats like ticket keys. Used only by the scan (write paths, card validation and masking, stub excerpts, task fields, doctor), never for ticket detection or counting; matched like `[jira] prefixes` (case-insensitive, word-bounded). Without `[jira] prefixes` every uppercase key is already exempt, so it changes nothing then |
| `[jira] projects` | `[]` | Phase 3 [plan]. Decided here: the Jira project keys the session's Jira tool should search (JQL `project in (...)`); detection never reads it. Reason: `prefixes` must also match keys of renamed or moved projects (detect `OLD-12`), which a search should not include |
| `[jira] no_ticket_branches` | `["develop","master","main","HEAD"]` | `[tickets]` |
| `[repos] roots` | `["~/projects","~/code","~/src"]`, each only when it exists | `~/projects` + `extra_roots` [plan] |
| `[repos] overview_roots` | the first existing root | v1 overview only in `~/projects` itself |
| `[repos] exclude` | `[]` | v1 `["triage"]` |
| `[caps] hook_output_chars` | 9500 | must stay < 10,000 [phase0] |
| `[caps] core_chars` | 4000 | |
| `[caps] global_rules_chars` | 5000 | |
| `[caps] global_rule_lines` | 80 | |
| `[caps] scoped_rule_lines` | 25 | |
| `[caps] overview_chars` | 3000 | |
| `[caps] pack_chars` | 4500 | |
| `[caps] max_full_packs` | 4 | |
| `[caps] last_message_chars` | 1500 | |
| `[caps] stub_excerpt_chars` | 400 | |
| `[caps] quote_min_chars` | 20 | |
| `[caps] run_max` | 100 | live vault writes per segment; `juno cap raise --by N --evidence "<user words>"` adds N (1-100) to one segment (ledger event `cap_raise`, not a vault write) |
| `[caps] rule_max_chars` | 300 | |
| `[caps] overview_cards` | 2 | |
| `[caps] pack_cards` | 2 | |
| `[caps] pack_threads_chars` | 500 | |
| `[caps] pack_knowledge_chars` | 300 | |
| `[caps] subagent_chars` | 3000 | |
| `[caps] subagent_global_rules_chars` | 2500 | |
| `[caps] pinned_rules_chars` | 800 | Pinned rules block on every main-session prompt; taken from `hook_output_chars` first |
| `[thresholds] meaningful_tool_calls` | 15 | |
| `[thresholds] meaningful_user_turns` | 5 | |
| `[thresholds] dirty_user_turns` | 10 | |
| `[thresholds] dirty_tool_calls` | 40 | |
| `[thresholds] reminder_spacing_turns` | 3 | |
| `[thresholds] active_ticket_days` | 14 | |
| `[thresholds] thread_stale_days` | 30 | doctor line |
| `[thresholds] stale_days` | 60 | stale memory |
| `[thresholds] profile_min_cards` | 3 | doctor profile warning |
| `[thresholds] stub_quiet_seconds` | 1800 | |
| `[thresholds] hook_error_repeat` | 3 | |
| `[thresholds] hook_error_window_hours` | 24 | |
| `[retention] ledger_days` | 7 | |
| `[retention] backup_days` | 30 | |
| `[retention] shadow_days` | 365 | v1 30; 365 per [plan Phase 5.3] |
| `[retention] asks_days` | 365 | Phase 6 I-B [plan] |
| `[retention] arm_segments_days` | 365 | `arms/segments.jsonl` (12.12) |
| `[retention] usage_days` | 365 | `usage.jsonl` (Phase 6.5) |
| `[decay] window_days` | 90 | `juno decay` |
| `[retention] error_log_bytes` | 1048576 | |
| `[shadow] enabled` | `true` | v1 code default false, live true |
| `[shadow] budget_ms` | 150 | |
| `[handoff] mode` | `"end"` | `"end"` or `"reminder"` |
| `[handoff] model` | `"sonnet"` | |
| `[handoff] budget_usd` | 0.3 | re-set once the v2 prompt size is measured [plan] |
| `[handoff] daily_budget_usd` | 2.0 | same |
| `[handoff] min_run_usd` | 0.05 | `saga_cards.MIN_RUN_USD` |
| `[handoff] timeout_seconds` | 240 | |
| `[handoff] digest_chars` | 16000 | |
| `[handoff] ticket_chars` | 6000 | the digest's per-ticket block (11.4), on top of `digest_chars`; `0` turns the `ticket` kind off |
| `[handoff] extract` | `true` | |
| `[handoff] claude_bin` | `""` (= `claude` on `launch_path`) | env `JUNO_CARD_CLAUDE` overrides (tests) |
| `[handoff] launch_path` | `~/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin` | `saga_cards.LAUNCH_PATH` |
| `[threads] scan` | `true` | |
| `[threads] stale_days` | 14 | |
| `[threads] gh` | `"gh"` | env `JUNO_THREADS_GH` overrides (tests) |
| `[threads] gh_owner` | `""` (no gh call) | v1 used a fixed company org; now set only per user |
| `[threads] gh_calls` | 6 | |
| `[threads] gh_timeout_seconds` | 10 | |
| `[links] linked_block` | `false` | the Linked block in packs (12.3) when no `linked` arm runs; while it runs, the arm's assignment decides |
| `[self] inject` | `false` | the `self` block while the newest `self` arm is stopped without a decision (12.12); never read while an arm runs |
| `[packs] purpose` | `false` | the profile Purpose section in repo packs (12.2) |
| `[packs] ranked_trim` | `false` | over-budget list sections keep their best-scored items instead of the newest (12.2) when no `ranked` arm runs; while it runs, the arm's assignment decides |
| `[keys] weight` | 0.0 | the fourth BM25 weight, for the fact keys of a passage (12.1); 0 turns keys off, both in ranking and in matching |
| `[keys] model` | `""` (= `[handoff] model`) | model of `juno keys backfill` |
| `[stale] auto_fix` | `false` | the switch of the weekly stale fixer (12.6.1); `juno schedule install` and a real `stale --fix` refuse while it is `false` |
| `[stale] weekday`, `start_hour`, `end_hour` | `"sun"`, 9, 21 | the schedule fires hourly in this window until the week has a successful run (13.9) |
| `[stale] model` | `"sonnet"` | model of the fixer |
| `[stale] file_budget_usd`, `run_budget_usd`, `timeout_seconds` | 0.40, 3.0, 600 | per file and per run spend caps, and the timeout of one model call |
| `[stale] max_files`, `max_changes_per_file`, `max_changes_per_run`, `max_lines_per_file` | 10, 5, 25, 12 | files checked per run, writes per file and per run, candidate lines sent per file |
| `[stale] branch_order` | `["develop","main","master"]` | the first existing `origin/<name>` is the default branch the fixer reads; a repo with no `origin` remote uses the first existing local `<name>` branch (decision 65) |
| `[stale] fetch` | `true` | `git fetch origin` before pinning (not run for a repo with no `origin` remote) |
| `[stale] busy_minutes` | 20 | `--job` defers while any session ledger changed within this window or a card run is active |
| `[voice] enabled` | `true` | the writing-voice feature (12.13); `false` collects, injects and checks nothing |
| `[voice] outward_tools` | generic MCP send/post/comment/reply/mail name patterns and `Bash(*gh pr comment*)`, `gh pr review` with a body, `gh issue comment`, `gh issue create`, `gh pr create`, tracker create/update issue, ticket and description tools | glob patterns on the tool name; `Bash(<glob>)` matches the command of a Bash call (12.13) |
| `[voice] formal_targets` | `gh issue create`, `gh issue comment`, `gh pr create`, tracker create/update issue, ticket and description tools, tracker add/create comment tools | same pattern syntax as `outward_tools`; an outward call that matches is checked and collected as `formal` when the request named no register, every other outward call as `voice` (12.13). An explicit key replaces the list |
| `[voice] draft_cues`, `formal_cues`, `critique_cues` | preset lists | phrases (matched like `correction_cues`) that mean "write text to send", "write it formally" and "that draft was wrong"; an explicit key replaces the preset list. A prompt is a draft request by these phrases or by a verb aimed at a target (12.13: preset-only `draft_verbs`, `draft_targets`, `standing_cues`) |
| `[voice] voice_cues` | preset list | phrases that select the `voice` register after a draft request in the segment (the one-word switch back from `formal`); same shape and matching as `formal_cues`; an explicit key replaces the preset list (#34) |
| `[voice] min_samples` | 15 | counted samples (`user` and `edited`) a register needs before it has a profile |
| `[voice] budget_chars` | 2400 | the fixed size of the injected drafting block |
| `[voice] import_max` | 300 | most messages one `juno voice import` batch takes (newest first by send time), and most imported samples kept in `voice.jsonl` (12.13, #38) |
| `[voice] import_max_age_days` | 365 | an imported message sent longer ago than this is skipped as too old (12.13, #38) |

Dropped keys: `guards`, `[known_gaps]`, `[thresholds] rule_grant_seconds`, `[mentees]`, `[ab] card_holdout` (ignored; doctor warns, see 12.12).

Constants that name nothing user-specific stay in code: caps inside extraction, regexes, queue sizes and eval thresholds. Port them with their v1 values from `saga_learn.py`, `saga_cards.py`, `saga_backlog.py`, `saga_threads.py`, `saga_topics.py`, `saga_search.py` and `saga_eval.py`.

- **Decided here: `[language] preset = "none"` on a fresh init.** Reason: the plan wants Turkish for the author and none elsewhere; the author sets `tr` by hand.
- **Decided here: with preset `none`, folding is `casefold()` plus NFC. With `tr`, the v1 Turkish map is applied first.** Reason: other languages get plain Unicode case-insensitivity, and the v1 behaviour is kept exactly under `tr`.
- **Decided here: `[repos] overview_roots`.** Reason: v1 restricted the overview to `~/projects`, and an extra root such as `~/work` never showed it. A key keeps that behaviour without hardcoding a path.
- **Decided here: `[shadow] enabled = true` by default.** Reason: the eval and the Phase 6 measurements need the data, and the log stays local in the state dir.

### 5.2 State dir

- macOS: `~/Library/Application Support/juno`. Linux: `$XDG_STATE_HOME/juno`, else `~/.local/state/juno`. Mode 0700. [plan]
- **Decided here: Linux uses `XDG_STATE_HOME`, not `XDG_DATA_HOME`.** Reason: the contents are rebuildable state and logs, which is what the XDG spec puts in the state home.
- `CLAUDE_PLUGIN_DATA` is not used for state. It may hold caches. [phase0 0.1]

| Path | Content | Retention |
|---|---|---|
| `ledgers/<sid>.jsonl` | segment ledger | `ledger_days` |
| `backups/<date>/` | file backups before writes, 20 versions per file | `backup_days` |
| `writes.jsonl` (and `.1`) | write log of the Phase 4 verbs (14) | rotated at 2 MiB |
| `arms/<name>.json`, `arms/log.jsonl`, `arms/arms.lock` | experiment arms and their log (12.12) | kept |
| `arms/segments.jsonl` | one row per segment that ran while an arm ran: `t, sid, arms, prompts, cues, counters {arm: {...}}`; counts only, no text or paths (12.12) | `arm_segments_days`, pruned with the other state |
| `cards/<sid>.job.json`, `<sid>.lock`, `runs.jsonl` | card jobs and run records | runs 30 d, jobs 8 d |
| `cards/candidates.json`, `self-candidates.json` (import batch only), `thread-candidates.json`, `thread-prs.json`, `backlog-suggestions.json` | queues, with a flock each | pending 30 d, decided 180 d |
| `topics/<sid>.<main|a-agent>.<name>` | topic/knowledge/pitfall claim markers (`<name>.again` is a pitfall's one re-fire) | with ledgers |
| `topic-fires.jsonl` | one `{t, name}` row per injected topic, knowledge, procedure or pitfall item (10.2) | past 256 KB cut to 7 days |
| `status.json`, `status/<sid>.json` | UI status | per-session files 1 d |
| `index.sqlite3`, `retired.json`, `links.json`, `triggers.json`, `stale.json` | derived caches; the index is schema 3 (12.1) | rebuildable |
| `keys.jsonl`, `keys.lock` | fact keys per passage hash, appended by the card run and `juno keys backfill` (12.1); not rebuildable without model calls | kept |
| `shadow.jsonl` | shadow log | `shadow_days` |
| `shadow-trim.jsonl` | ranked-trim shadow log (12.2): `ts, segment, on, sections` | size-capped at 1 MiB, one rotation |
| `asks.jsonl` | one row per answered AskUserQuestion question: `t, sid, tid8, q, n_options, rec, picked, other, multi`, plus `arms` while an arm runs; no question or answer text | `asks_days`, pruned with the other state |
| `usage.jsonl` | durable positive-use trace, one row per signal: `t, sid8, rel, ev` with `ev` `read` (PostToolUse Read, or Grep/Glob with a path under the vault, main or subagent), `card` (a vault file linked in a card the run wrote) or `cite` (a vault file named in extraction evidence); pack delivery is exposure and is not logged | `usage_days`, pruned with the other state |
| `voice-names.json` | casefolded names seen in the user's own prompts, for the voice example filter (12.13); mode 0600, at most 2,000 names; never copied to the vault | kept |
| `voice.jsonl` | writing-voice samples and corrections moved out of the ledgers at session end, and imported samples (12.13): `k` (`sample` or `fix`), `id`, `t`, `sid`, `reg`, plus `kind`, `by` (`user`, `edited`, `claude`, `import`), `text` or `quote`, `draft`; mode 0600, at most 400 live samples, 100 corrections and `[voice] import_max` imported samples | kept, newest rows only |
| `selftest.json`, `selftest.lock` | the questions of `juno selftest` (12.14): `next`, `questions[]` of `id`, `question`, `expect`, `sentence`, `control`, `added`; mode 0600, at most 200 questions; never copied to the vault | kept |
| `eval/` | labelling sets, frozen sidecars, `last-gate.json` | kept |
| `hook-errors.log`, `hook-timings.log`, `debug.log` | errors and timings | rotated at `error_log_bytes` |
| `counters.json`, `vault.lock` | counters, the write lock | - |
| `.juno-tmp-*` (state root, `ledgers`, `backups`, `status`, `topics`, `cards`) | temp files left by an atomic write killed at a hook timeout | swept by the prune after 1 day |
| `install-manifest.json`, `install-backups/` | settings merge record | until uninstall |
| `dashboard/` | page, `usage.json`, caches | rebuilt |

There is no `grants/` directory. Deleting the state dir loses backups, unwritten stub material, queues, shadow data and the manifest. Memory itself is safe [v1: Layout].

## 6. Hooks

### 6.1 Wiring

`hooks/hooks.json` in the plugin registers each event with one command:

```
"\"${CLAUDE_PLUGIN_ROOT}/bin/juno-hook\" <event>"
```

`${CLAUDE_PLUGIN_ROOT}` is quoted [phase0 0.1 validate]. `bin/juno-hook` is POSIX sh. It:
1. Reads the interpreter from `JUNO_PYTHON`, else from the first `python = "<path>"` line of the config file. One `sed`, no TOML parser.
2. Without a config file, prints a fixed SessionStart JSON line `Juno is not initialised: run "<plugin root>/bin/juno" init` on SessionStart only, and exits 0 on every other event.
3. When the interpreter is not executable, exits 0.
4. Runs `"$py" -I -S "$root/juno/hooks/<event>.py"`, then `exit 0` whatever the status.

- **Decided here: an sh launcher reads the interpreter from config with `sed`.** Reason: `hooks.json` must stay static [plan], and sh cannot parse TOML. `juno init` writes `[runtime]` as the first table with one `python = "..."` line so the `sed` match is exact. Doctor checks that the line parses the same way in both readers.
- Entry scripts put the plugin root on `sys.path` themselves, because `-I` adds no script dir.

Timeouts are carried from [v1: Permissions (auto mode)]: SessionStart 3 s and 5 s, SubagentStart 5 s, UserPromptSubmit 3 s, PreToolUse 3 s, PostToolUse 5 s, PostToolUseFailure 3 s, Stop 3 s. SessionEnd's real budget is 1.5 s; plugin hook timeouts do not raise it [phase0 0.1].

### 6.2 Per event

Every event does the following:
- Checks `JUNO_DISABLE=1` first, then whether the session is interactive [v1: Sessions and segments].
- Applies the synthetic-prompt filter [v1: Sessions and segments].
- Writes JSON to stdout only when it has context to add.
- Logs errors to `hook-errors.log` and timings to `hook-timings.log`; an OSError line ends with `errno=N`.
- Reads stdin as UTF-8 with `errors="replace"` and replaces lone surrogates in payload strings, ledger records and its output with U+FFFD.
- When a state-dir write fails with ENOSPC, EACCES, EPERM or EROFS, SessionStart and UserPromptSubmit still inject the vault-derived context (rules, pins) plus one line saying Juno could not write its state dir; Stop stays silent. Other OSErrors are not caught. Writes fail closed: UserPromptSubmit drops a `juno-unrecorded-<uid>-<hash>` flag in both `$TMPDIR` (when set) and `/tmp`, and `ledgerio.fold` treats the segment as `[nosave]` if the flag exists in either, so write verbs refuse.
- Always exits 0.

Non-interactive sessions get nothing and write nothing.

| Event | Matcher | Does | Source |
|---|---|---|---|
| SessionStart (handler `rules`) | - | retention prune; core.md and global rules as RULES | [v1: Phase 1 hooks], [v1: Injection] |
| SessionStart (handler `context`) | - | refresh `plugin_root` when `CLAUDE_PLUGIN_ROOT` differs; stub pass and late runs (max 2); `_generated/learned.md` rebuild when stale; overview or repo pack; previous-segment line; repeated hook-error line; the weekly report line; the stale fixer line (12.6.1); general pitfalls (5 lines, 1,200 chars); rule, thread candidate nudges (4 each, 1,600 chars); ideas line; local time. The context opens with the segment line and a `Juno vault: <absolute path>.` line resolved from the config, so a model reads vault files at the real path. The stub pass and nudges are skipped after compact. | [v1: Injection], [v1: Enforcement], [v1: Session-end cards] |
| SubagentStart | - | subagent header, RULES (global ≤ 2,500, ticket/repo rules, delivered topics), CONTEXT pointer; ≤ 3,000 chars; nothing for headless parents | [v1: Injection] Subagents |
| UserPromptSubmit | - | the pinned-rules block first (10.4a); append the typed prompt (paste-stripped, redacted, `prompt_id`); count the turn; record the session title; `[nosave]` (flag and its one-line notices, see 8.1a); ticket packs (only with `[jira]`); correction cue line; the drafting block when the prompt asks for text to send, and the `voice_ask` and `voice_fix` records (12.13); topic rules, knowledge and pitfalls on prompt triggers; shadow record (150 ms, main session) | [v1: Phase 1 hooks], [v1: Phase 3] |
| PreToolUse | `Edit\|Write\|MultiEdit\|NotebookEdit\|Bash\|Read\|Glob\|Grep\|AskUserQuestion\|mcp__.*` | topic rules, knowledge, pitfalls on tool triggers (claimed per segment and agent); AskUserQuestion `ask` record (clean or prefilled, main session); on an outward tool (`[voice] outward_tools`), the non-blocking voice check line and, once per segment, the drafting block (12.13); refusal of Edit/Write/MultiEdit/NotebookEdit on any vault path and on Juno's own state (section 14.4), and of a subagent Bash call whose marker cannot be recorded (8.3); these are the only denies Juno issues. A write tool outside both falls through to the topic triggers. | [v1: Injection] Topic rules, [v1: Rules policy] AskUserQuestion answers |
| PostToolUse | `*` | ledger append (repos, tickets, tool calls, edits, card writes, boundaries; one `voice` record per main-session outward write (12.13); ticket keys of `[jira] prefixes` in the inputs of a main-session MCP tool call (`issueIdOrKey`, `issueKey`, `key`, ...) and the `key` in the response of an MCP `create` tool, first 8 KB scanned, count as segment tickets like a repo hit; a subagent call does not); one `usage.jsonl` line per vault Read, Grep or Glob, main or subagent (an I/O error is ignored); first-touch repo pack; `card_dirty`; AskUserQuestion answer texts after a clean ask, and one `asks.jsonl` row per answered question (after the ledger write; an I/O error there is ignored); status files | [v1: Phase 1 hooks], [v1: Boundaries] |
| PostToolUseFailure | `*` | ledger `fail {tool, error class}`, used for the hook-error and correction signals | [phase0 0.1 open items] |
| Stop | - | store the last assistant message (1,500 chars, redacted) and status; in mode `reminder`, the v1 reminder | [v1: Enforcement] Stop reminder |
| SessionEnd | - | the voice step first, in either mode: samples to `voice.jsonl`, `self/voice.md` rebuilt (12.13); then, in mode `end`, the job decision and a detached spawn (6.4); ledger `end {reason, skip}` | [v1: Session-end cards] Hook |

- **Decided here: PreToolUse is one handler group with a combined matcher instead of v1's three.** Reason: without the guard, every group runs the same trigger and record path, so one group means one process per call.
- **Decided here: PostToolUseFailure matches every tool and only appends a ledger event.** Reason: v1 used it only for grant cleanup, and grants are gone. [phase0] keeps it for hook-error signals.
- **Decided here: PreToolUse has no backup-before-Edit/Write; the v1 and Phase 2 backup is removed.** Reason: from Phase 4 no Edit/Write reaches a vault file, so the backup is unreachable; the write verbs back up the file themselves and `juno undo` uses the write log (14).

Injection behaviour for packs, budgets, topic dedupe, the Linked block and stale markers is carried verbatim from [v1: Injection] and [v1: Stale memory detection] item 5. The changes:
- Packs and overview: the ticket parts apply only with `[jira]`.
- The overview appears only at the top of an `overview_roots` directory.
- The profile Purpose is left out of repo packs unless `[packs] purpose = true` (Phase 6.6). `project purpose` still writes it and search still finds it.
- The Linked block is decided by the `linked` arm while it runs, else by `[links] linked_block` (default off; Phase 6.7). **Decided here: `[ab]` gains no key for it until Phase 6.** Reason: the plan makes it an arm, and arms are created by pre-registration files, not by config.
- The pack sources include the owner's `## Decisions` and, until `juno migrate --decisions` ran, the legacy `decisions/<topic>.md` `## Current` (12.2). **Decided here.** Reason: decisions are now stored per topic, and the pack has to keep showing them.

### 6.3 Imports per event

Each entry imports only what its event needs. A test imports each entry in a fresh interpreter (`-I -S`) and checks `sys.modules` against the event's banned list. `-X importtime` per entry is printed by `tests/run.py --importtime` and tracked. [plan]

| Event | Banned (beyond the common list) | Allowed, notable |
|---|---|---|
| common to all | `sqlite3`, `difflib`, `argparse`, `unittest`, `email`, `http`, `urllib`, `ssl`, `socket`, `asyncio`, `multiprocessing`, `concurrent`, `juno.cli`, `juno.core.learn`, `juno.core.search`, `juno.core.eval`, `juno.core.backlog`, `juno.core.stale` (the run, not the cache reader) | `json`, `re`, `os`, `time`, `hashlib`, `tomllib`, `juno.config`, `juno.core.fold` |
| SessionStart | `juno.core.cards` (run part) | `subprocess` (late-run spawn, git), `shutil`, `juno.core.threads` (nudge), `juno.core.packs`, `juno.core.links` |
| SubagentStart | `subprocess`, `shutil`, `juno.core.cards` | `juno.core.packs` (rules part) |
| UserPromptSubmit | `subprocess`, `juno.core.cards` | `sqlite3` and `juno.core.search` only inside the shadow call (lazy) |
| PreToolUse | `subprocess`, `shutil`, `juno.core.cards`, `juno.core.packs` | `juno.core.topics` |
| PostToolUse | `juno.core.cards` | `subprocess` (git branch, 1.5 s), `juno.core.packs` |
| PostToolUseFailure | `subprocess`, `shutil`, `juno.core.packs`, `juno.core.topics` | - |
| Stop | `subprocess`, `shutil`, `juno.core.packs`, `juno.core.topics` | - |
| SessionEnd | `juno.core.packs`, `juno.core.topics`, `juno.core.cards` (run part), `secrets` | `subprocess` (Popen), `hmac` not needed (the token is generated with `os.urandom`) |

- **Decided here: this table.** Reason: the plan fixes the mechanism and one example (no `sqlite3` in PreToolUse, allowed in UserPromptSubmit for shadow search). The rest follows the v1 import measurements [phase0 0.3], where `subprocess`, `shutil` and the cards and threads modules were the event-specific costs. Phase 0 shows import time is not the bottleneck, so the list exists to protect the narrow import graph and test selection, not milliseconds.
- `juno.core.cards` is split into `cards.route` (hook-safe: the routing and job decision) and `cards.run` (the detached child only).

### 6.4 SessionEnd and the detached child

- The conditions are those of [v1: Session-end cards] Hook: mode `end`; an interactive main session; a meaningful segment with `card_dirty`; no `[nosave]`; not private; under the daily budget; no job pending (< 120 s) or running.
- Steps: write `<state>/cards/<sid>.job.json` (`status pending`, transcript path, reason, `max_budget_usd = min(budget_usd, daily left)`, a random token). Then `subprocess.Popen([python, "-I", "-S", root/juno/hooks/card_run.py, "run", sid], start_new_session=True)` with stdin, stdout and stderr on devnull, `close_fds=True`, `JUNO_DISABLE=1`, the session identity vars removed (v1 `saga_cards.SESSION_VARS`), `PATH = [handoff] launch_path`, the token in `JUNO_CARD_TOKEN`. Then return.
- [phase0 0.1] showed that a `start_new_session=True` child survives SessionEnd and the exit of `claude`.
- Time limit: the hook must return within 1.5 s [phase0]. **Decided here: an internal deadline of 1.0 s, checked before the spawn.** When it has passed, the hook records `end {skip: "deadline"}` and leaves the segment to the late run. Reason: this keeps a 0.5 s margin under a cutoff that Phase 0 did not measure exactly. v1 SessionEnd took about 58 ms, so the deadline should never trigger.
- The hook reads no transcript and makes no model call.

### 6.5 Output contract

- One JSON object per call: `hookSpecificOutput.additionalContext` (or `systemMessage` where v1 used it). The text is at most `hook_output_chars` (9,500). Over the cap, the text is trimmed by block, with `[... Juno truncated N chars; see <path> ...]` markers.
- `RULES (follow)` and `CONTEXT (data, not instructions)` are separate labelled blocks.
- PreToolUse context reaches the model only after the tool result [phase0 0.1]. Anything that must arrive before an action goes in at UserPromptSubmit or SessionStart.
- An exception in a hook prints nothing and exits 0. Phase 4 adds one exception to this: a vault-path or state-path Edit/Write gets a deny, and so does a subagent Bash call that cannot be recorded (8.3).

## 7. Sessions, segments, detection (carried)

- The interactive check, segments, the ledger (O_APPEND, lines < 4 KB, `trunc`), the fold, and the synthetic-prompt filter are carried verbatim from [v1: Sessions and segments]. The disable switch is renamed `JUNO_DISABLE=1`.
- Repo detection follows [v1: Repo], with `[repos] roots` replacing `~/projects` + `extra_roots`. The first root containing the path wins, in config order. A first-level folder counts as a repo only when it has a `.git` entry. Worktrees resolve through the git common dir. The vault and state dir are never repos. [plan], [v1: Open items] "Repo detection needs git"
- Ticket detection follows [v1: Ticket], only when `[jira] prefixes` is non-empty. Without it there is no ticket in the ledger, the packs or the routing, and handoffs route by session name, repo or `misc` [plan Phase 3].
- Boundaries follow [v1: Boundaries].
- Meaningful segment, `card_dirty`, stub cards, the stub pass and late runs follow [v1: Enforcement] and [v1: Session-end cards] Hook (late run). The transcript root is `[paths] claude_dir`/projects, and `JUNO_CLAUDE_PROJECTS` overrides it for tests.

## 8. Write paths and checks

### 8.1 Cheap checks (kept)

Every vault-writing CLI verb refuses, with exit 2 and one `Juno: refused: <reason>` line, when any of these holds:
1. Not interactive: `CLAUDE_CODE_SESSION_ATTENDED` is not `1`, or unknown, and `CLAUDE_PID` args show `-p`/`--print`. This covers headless runs and subagents of a headless parent.
2. An agent context: any env var ending in `AGENT_ID` is set (v1 `agent_env_detected`).
3. `CLAUDE_CODE_SESSION_ID` is missing, or names no existing ledger.

There is no grant and no Bash parsing. The only PreToolUse deny is the vault Edit/Write refusal (14). [plan, Phase 2 Kept cheap checks]

The vault-writing verbs are `rule add|remove|move|triggers|accept|reject`, `thread close|keep`, `backlog add|add-to|edit|set|log|done|drop|accept|reject`, `snapshot`, `compact`, `digest`, `profiles`, `doctor --purge-synthetic` and the Phase 4 verbs of 14 (`card`, `knowledge add`, `note`, `decision`, `project`, `procedure`, `pitfall`, `idea`, `capture`, `self`, `fix`, `link`, `forget`, `undo`; `thread add` is logged too). Every write takes `vault.lock` (2 s CLI, 5 s views), makes a backup first, and writes atomically [v1: Phase 2 item 8].

### 8.1a The `[nosave]` token

The segment flag is set (sticky for the segment, see the ledger `prompt` record) only when `[nosave]` is the first or the last whitespace-separated word of the user's typed prose. `core/prompts.py::nosave_set`:
- Typed prose is the user's text (system, task and command blocks already removed) without `<pasted_content>` blocks, `[Pasted text ...]` and `[Image #N]` markers, log-like lines, fenced code blocks (``` and ~~~) and inline code spans (backtick runs of any length). Leading and trailing blank space is ignored.
- Case is ignored. The word may be wrapped in leading `(` `"` `'` `*` `>` and trailing `.` `,` `;` `:` `!` `?` `)` `"` `'` `*`. `[nosave]s` and `x[nosave]` do not count. A message that is only the token counts.
- A mention anywhere else does not set the flag. Text pasted without a marker is indistinguishable from typed text, so the position rule is its only guard.

The UserPromptSubmit hook adds one CONTEXT line, first in its output (after the pinned-rules block, 10.4a), in the turn that decides it, and none once the segment flag is already set: `Juno: [nosave] is set for this segment: nothing is written to the vault and no handoff card is made.` when this prompt sets the flag, or `Juno: [nosave] was mentioned but not set; it counts only as the first or last word of a message.` when the token stands in the typed prose but not at an end (code and marked pastes give no notice).

### 8.2 Quotes

- The quote is checked against the segment's quote sources: typed prompts (paste-stripped) and chosen AskUserQuestion answers: the selected option labels and the free text, never the option descriptions. It needs `quote_min_chars`, a verbatim folded substring, and, for `rule add|remove|move|triggers` only, the meaningful-word overlap [v1: Rules policy] Quote, AskUserQuestion answers.
- **The quote is required** for `rule add|remove|move|triggers` and for `backlog add|add-to|edit|set|done|drop` [plan: rules "changed only with the user's quote"], [v1 backlog: 4].
- `snapshot` needs no quote. **Decided here.** Reason: the user's CLAUDE.md lets Claude snapshot without asking once the tests pass, v1 ran with quotes optional (`guards = false`), and a snapshot changes no memory.
- `rule accept` keeps the v1 check that each id appears in a typed prompt or chosen answer of the segment, plus the re-verification against the source ledger. **Decided here.** Reason: the SessionStart nudge puts the ids into the AskUserQuestion labels, so the check costs the user nothing and keeps acceptance in the user's hands. The same applies to `thread close` and `backlog accept`; not to `thread keep`, `backlog reject` or `rule reject`, because a multiSelect answer carries only the chosen ids (Phase 2 finding).
- `thread close --line N --was TEXT --evidence QUOTE` closes one open thread line directly when the user asks. **Decided here.** Reason: `forget` refuses threads.md, and a candidate id exists only for lines the scan flagged. The line must be a thread line containing `--was` (8+ characters); the evidence is the user's own words (`gate.anchor` with `user_only`, as `decision` and `self`; repo:/ticket: anchors are refused). It writes through the gate (verb `thread-close`, a write id, `juno undo <id>` restores the line) and records `thread_closed`. It cannot be mixed with candidate ids.

### 8.3 Accepted gaps

- A Bash write that bypasses the CLI (`echo >>`, an interpreter) is not detected. The threat model is noise, not an adversary, and recovery is by snapshot. [plan]
- A subagent shell has no agent variable (Phase 2 probe: its env equals the main session's). So PreToolUse records each subagent Bash call (payload `agent_id`) as `<state>/agent-calls/<sid>.<tool_use_id>`, and `write_refusal` refuses while one is in flight (`agent context: a subagent tool call is in flight`). The marker holds its expiry: the call's `timeout` (default 120 s) plus 30 s, capped at 600 s; a `run_in_background` call keeps its marker until 600 s and PostToolUse does not clear it. PostToolUse and PostToolUseFailure clear the call's marker and remove every expired one, and the SessionStart prune does the same, so a denied or interrupted call blocks the main session only until its expiry. When the marker cannot be written, PreToolUse denies that subagent Bash call (fail closed). It does not parse Bash. Cost: a main-session write is refused while a subagent's Bash call runs; a background command that outlives 600 s is not covered.
- Customer data detection is one scanner (`privacy.scan`): secrets, email, phone and 6+ digit ids. URLs are checked for an email anywhere (also percent-encoded) and for phone or id values in the query and fragment; the path is not checked. Ticket keys of the configured `[jira] prefixes` (any uppercase key shape without prefixes) and of `[privacy] allow_prefixes` are exempt; every other `WORD-digits` token, such as `customer-123456789` or `CUSTOMER-123456789`, is an id. The only other `WORD-digits` exemption is the timestamp shape: an uppercase prefix whose digits start like a date, `20YYMM` plus 2-8 digits (`INS-202611021105`), whatever the prefixes. A number directly followed by a unit (`bytes`, `B`, `KB`, `MB`, `GB`, `ms`, `s`, `sec`, `seconds`) is exempt, as is one preceded by `comment`, `review`, `issue`, `run`, `PR`, `pull`, `build` or `job` (fixed list), optionally with `#`, `(`, `:` or `=` and spaces between (`comment (1234567)`, `run: 12345678`). A run of 21 or more digits is never an id (not a phone, not a customer id), also inside a hyphenated name (`example-service-20260101120000000000000001`); shorter digits in a hyphenated name stay ids. Doctor drops the trailing ` · <seg8>` of `## ` card headings before scanning, as card validation does. The same scanner runs on every write path: the verbs, rules (text and quote), backlog and thread text, the card run, stub excerpts (withheld as `-`), task fields and `--url`, extraction, and doctor [v1: Decisions] Doctor privacy.

### 8.4 Settings permissions (via `juno init`, section 13)

- The settings merge adds `allow: Read(<vault>/**), Edit(<vault>/**)` and `deny: Edit(<vault>/rules/**), Edit(<vault>/.git/**)` [v1: Permissions (auto mode)]. The `**` glob covers `rules/repos/` and `rules/topics/` too. **Decided here: keep the two deny entries.** Reason: they are a Claude Code permission, not a Juno denylist. They cost nothing at runtime and enforce "rules change only through the quote" for Edit/Write without a hook.

## 9. CLI verbs (Phase 2 Carry)

The shim is `juno <verb>`. Each verb with `--json` prints one JSON object. Exit codes: 0 ok, 1 FAIL (doctor, eval gate), 2 refused or invalid. Read-only verbs are allowed for subagents and need no checks.

| Verb | Writes | Contract (port from) |
|---|---|---|
| `rule add --scope global\|repo:<r>\|topic:<slug> [--triggers "a,b"] [--pin] --quote Q "<rule>"` | `rules/` | scopes, triggers, caps, ` · ` ban, secret scan, line format; `ticket:` refused [v1: Rules policy] |
| `rule list [--json]` | - | numbered lines per file (read-only); a pinned rule shows `[pinned]`; `--json` keeps `rules` as strings and adds a parallel `pinned` list of booleans per file |
| `rule pin\|unpin --scope S --quote Q N` | `rules/` (through the write gate) | sets or clears the ` · pinned` mark on rule N; same quote evidence as `remove`; prints a write id, `juno undo <id>` reverts it; pinning a pinned rule (or unpinning an unpinned one) is a no-op; the user's own decision, never automatic: `rule accept` never pins; `add --pin` adds and pins in one write, and `add --pin` of a rule already present pins that rule (own write id) |
| `rule remove --scope S --quote Q N` | `rules/` | [v1: Rules policy] Remove |
| `rule move --from S --to S [--triggers] --quote Q N` | `rules/` | [v1: Rules policy] Move; `--from ticket:KEY` moves a rule out of a legacy `rules/KEY.md` |
| `rule triggers --scope topic:<slug> --add "a,b" --quote Q` | `rules/` | [v1: Rules policy] Topic triggers |
| `rule pending [--json]` / `accept <id...>\|--batch memory-import` / `reject <id...>\|--batch memory-import` | `rules/`, queue | [v1: Rules policy] Candidates |
| `triggers propose` | rule queue (`cards/candidates.json`) | session (not agent context) | queues a `kind: "triggers"` candidate per knowledge file without `triggers:` (10.3); writes no knowledge file |
| `thread pending [--json]` / `close <id...>` / `close --line N --was TEXT --evidence QUOTE` / `keep <id...>` | `threads.md`, queue | [v1: Session-end cards] Thread close candidates |
| `backlog add\|add-to\|edit\|list\|show\|set\|log\|done\|drop\|accept\|reject` | `backlog/`, `tasks/<K>.md` Notes line, queue | [v1 backlog: 5. CLI]; the body file must be under `/tmp` or the state dir; it is deleted only after the write succeeded (same for `arm start --prereg`), a refused call leaves it |
| `search "<q>" [--repo] [--ticket] [--since D] [--until D] [--limit 5] [--archive] [--json]` | index only | section 12.1 |
| `recap [--days 7 \| --week] [--ticket] [--json]` | - | [v1: Phase 2 item 4] |
| `task set <KEY> --title T --status S [--url U] [--slack-checked YYYY-MM-DD] [--repo R]...` | `tasks/<KEY>.md` | Phase 3 [plan]; needs `[jira] prefixes`, KEY must match them; write check (8.1); new file needs title and status, an existing file takes any subset; status is lowercased with `-` for spaces (`In Review` -> `in-review`), may hold Unicode letters (`Kapandı` -> `kapandı`, `İptal` -> `iptal`; a capital `I` becomes `ı` only when the word has another Turkish letter), one word of letters, digits, `.`, `_`, `-`, 1-40 chars, starting with a letter or digit; title: one line, 200 chars, secret scan; url `http(s)` only, default from `base_url`; frontmatter `jira, title, status, url, repos, created, updated`, other keys and the body are kept; no network |
| `task log <KEY> --section S --key K [--evidence E] "line"` or `--batch-file F` | `tasks/<KEY>.md` `## <S>` | user words or `ticket:<KEY>` of that ticket | 14.1 |
| `task show <KEY> [--json]` | - | prints the file (or its fields) |
| `tasks [--status] [--repo] [--json]` | - | [v1: Phase 2 item 4]; prints "jira not configured" without `[jira]` |
| `ideas [--status] [--tag] [--json]` | - | [v1: Decisions] ideas |
| `stale [--days N] [--json]` | `stale.json` | section 12.6 |
| `stale --fix [--dry-run] [--limit N] [--max-usd X] [--file REL] [--job] [--json]` | vault (through the gate), `jobs/stale-weekly/runs.jsonl`, vault `.git` | section 12.6.1; calls the model; a real run needs `[stale] auto_fix`, schema 2 and no Claude session; `--dry-run` writes only the run row |
| `schedule install\|remove\|status [stale-weekly] [--json]` | `~/Library/LaunchAgents` or `~/.config/systemd/user` | section 13.9 |
| `decay [--days N] [--json]` | - | Phase 6.5; read-only: knowledge, procedure, note and idea files (not private, archived or retired) with no `usage.jsonl` signal within the window, counted from max(file `created` else mtime, the trace's first row); there is no mover |
| `doctor [--purge-synthetic] [--json]` | state; ledgers with `--purge-synthetic` | section 13.6 |
| `snapshot [-m msg] [--json]` | vault `.git` | [v1: Phase 2 item 6]; no quote (8.2) |
| `compact` | `handoffs/`, `archive/handoffs/` | [v1: Phase 2 item 5]; archive path per 4.3 |
| `digest` | `_generated/learned.md` | [v1: Enforcement] Digest |
| `profiles` | tickets block in `projects/*.md` | [v1: Phase 2 item 3]; no-op without `[jira]` |
| `eval sample\|freeze\|floor\|gate\|recall\|agree\|compare\|ab` | `eval/` | section 12.9; `compare --setting keys [--weights 0,1,2,4]` is the paired offline comparison; `ab` is an alias of `arm status` |
| `selftest add "<q>" (--expect PATH \| --control) [--sentence S] [--json]` / `list [--json]` / `remove <id> [--json]` / `run [--limit K] [--budget CHARS] [--json]` | `selftest.json` (state) | section 12.14; `add` and `remove` refuse like `voice import`; `list` and `run` read only |
| `keys backfill [--limit N] [--max-usd 2.0] [--dry-run] [--json]` / `status [--json]` | `keys.jsonl` | section 12.1; `backfill` calls the model, in the foreground, and is refused in an agent context |
| `arm start <name> [--feature F] --prereg F` / `status [--json]` / `stop <name> --reason R` / `decide <name> [--json]` | `arms/` | section 12.12; `start` and `stop` need the interactive main session; `F` under `/tmp` or the state dir, deleted when read |
| `dashboard [--out] [--json] [--open]` | `dashboard/` | section 12.10 |
| `init`, `uninstall`, `export`, `import`, `migrate` | settings, vault, state | section 13 |
| `import-memory [--dry-run]` | `projects/`, `knowledge/`, rule queue | section 13.11 |

- **Decided here: `saga decisions` is not carried.** Reason: `decisions/<topic>.md` replaces the generated `decisions.md` [plan]. Topics are written by `juno decision`. A generator would overwrite them.
- **Decided here: `--archive` makes search include `archive/` (indexed, flagged).** Without it, archive is excluded. Reason: the plan says the archive is "searched only with `juno search --archive`". v1 skipped `archive/` entirely in the walk.

### 9.1 Phase 4 write-gate verbs

Specified in section 14: `card`, `knowledge add`, `note`, `decision`, `project`, `procedure`, `pitfall`, `idea`, `capture`, `self`, `fix`, `link`, `forget`, `undo`. `thread add` (9) also writes through the gate. `task set` stays a Phase 3 verb; `task log` is gated (14.1).

### 9.2 `saga` compatibility shim

History: removed by decision 61.

## 10. Rules, topics, knowledge, pitfalls

### 10.1 Scopes and files
These are carried from [v1: Rules policy] Scopes: `global`, `repo:<repo>`, `topic:<slug>` (slug `^[a-z0-9][a-z0-9-]{1,39}$`). Legacy `rules/<KEY>.md` files load read-only in ticket packs, only with `[jira]`, and doctor warns about each one. Repo names that are ticket-shaped or are `global`, and ticket-shaped topic slugs, are refused. A session-end rule candidate whose text holds a ticket key (a segment ticket, or an uppercase `PREFIX-digits` key outside a short standard-prefix allowlist such as `UTF-8` or `SHA-256`) counts `invalid`, and a new topic's triggers drop `GENERIC_TRIGGERS` words. Files live at `rules/global.md`, `rules/repos/<repo>.md` and `rules/topics/<slug>.md`; the folder, not a filename prefix, says the scope. [user decision 2026-11-07: no `topic-` prefix]

### 10.2 Triggers
These are carried from [v1: Rules policy] Topic triggers and [v1: Injection] Topic rules / Knowledge / Pitfalls / Topic dedupe:
- At most 20 per topic, 2-60 chars, case-folded literals, no user regex.
- An alphanumeric end needs a word boundary. A punctuation end is a substring match.
- Matched against the typed prompt, the Bash command, `file_path|path|notebook_path`, the Glob `pattern`, the Grep `glob` and the tool name.
- Bash commands are matched on their own tokens: heredoc bodies, `--body`/`--body-file`/`--message`/`--notes`/`--title`/`--description`/`--comment`/`--text`/`--search`/`--query` values, `git|gh -m`/`-F` messages, grep/rg patterns that are regexes or word lists, and every positional argument of the memory CLI's own verbs (`juno search "..."`, `juno rule add ... "text"`) are dropped first (data, not intent) [#36].
- A path (`file_path|path|notebook_path`, the Glob/Grep pattern without its wildcards, and a Bash word with a `/` or a file extension) matches a trigger by whole path segments: `tracker` matches `/x/tracker/a.md` and `/x/tracker.md` (the name without its extension) but not `/x/tracker_brief.py`; a trigger with a `/` matches consecutive segments; a trigger that starts with `.` and has no `/` matches an extension. A path match is specific enough alone [#36].
- A topic rule or knowledge item matched only by single plain words (letters only: no digit, no `-_./`, no phrase) also needs the call's text to share a word with the item: the BM25 (`core/rank.py`, the item alone as corpus, no index) of the call against the item's text (rule texts, or title, summary and body, plus its triggers and slug) must be at least 0.25, which is one shared word of four or more characters other than the matched trigger. A flag, path, dotted, hyphenated, underscored or digit term, or a phrase of two or more words, is enough alone. Procedures and pitfalls are not gated this way [#36].
- With `[language] preset = "tr"` the call's words are also tried with Turkish case and plural endings and apostrophe suffixes removed (up to three endings, a stem of 3+ letters, after folding: `lar ler in un nin nun a e ya ye na ne i u yi yu ni nu da de ta te nda nde dan den tan ten ndan nden`), so `trackerin`, `trackerte` and `deploy'u` match `tracker` and `deploy`; with no preset the text is matched as typed [#36].
- A pitfall fires on a match of one trigger that is specific by shape (a flag, path, dot, hyphen, underscore, digit, or a phrase with a non-stoplist word) or equal to the command's executable (first word of a pipeline segment), or on two non-stoplist triggers. A single plain word never fires it; `juno doctor` warns about such lines. Stoplist words (`GENERIC_TRIGGERS`) never count as specific.
- The index is `triggers.json`, rebuilt on a size or mtime change of `rules/topics/*.md`, `knowledge/*.md`, `procedures/*.md` or `pitfalls.md`.
- A procedure's `triggers` and `repos` are front matter; the match injects `[Procedure · procedures/<slug>.md] <title>` plus its numbered steps, at most 600 chars (a step that does not fit is cut), as CONTEXT.
- Each item is injected once per segment and agent, through an exclusive marker. A block that does not fit is skipped unclaimed. A claimed pitfall re-fires at most once more per segment (a second marker, `<name>.again`), and only when the call repeats its error: the last `: ` phrase of its evidence (2+ words, 8-80 chars) or a distinctive token (6+ chars with one of `_./-` or a digit) from the part after the first `: `, never an interpreter or tool name (`python3`, `node`, `bash`, ...) nor a token the pitfall's own text also uses [#36].
- Each injection is counted in `state/topic-fires.jsonl` (`{"t", "name"}`, pruned past 256 KB to the last 7 days); `juno doctor` lists the 5 most fired names of the last 7 days (INFO) and warns about each knowledge file without `triggers:` [#36].

### 10.3 Knowledge and pitfalls injection
As in v1: `[Knowledge · knowledge/<name>.md] <title>` plus the summary or the first two lines (240 chars each), and `[Pitfall · pitfalls.md] <text>`. General pitfall lines appear at SessionStart. Retired, archived and private files are skipped.

A knowledge file without `triggers:` is never injected by a matching call (`juno doctor` warns about it, 10.2). When a session reads one by hand (a `read` row in `usage.jsonl`: Read, Grep or Glob on the file), the session-end run (no model call) queues a **trigger candidate** in `cards/candidates.json` (`kind: "triggers"`, `file`, `triggers`, `source: "session-end"`): up to four terms from its title and body that are specific by shape, in the title or said twice (no stoplist word, no `GENERIC_TRIGGERS` word, plain words of 5+ letters), at most 3 files per run, one pending proposal per file, a decided one not proposed again for 180 days. Both proposal paths drop ISO dates, ticket-shaped ids (`[jira] prefixes`, else `PREFIX-digits` with an uppercase prefix) and every token found in more than a quarter of the visible knowledge files (counted only from 4 files up, so a tiny vault keeps its tokens); path-like tokens are split on `/` and each part is checked (a token with a rejected part is dropped whole), as are hex hashes (7-40 hex chars with at least one digit and one letter, so `added` and `face` stay) and uuids, tokens starting with a vault folder (`handoffs/...`) or `origin/`, and the slug of any other topic file in the vault; a plain word (letters only) must occur at least twice in the file and in fewer than 3 other knowledge files; a file whose candidates all drop queues nothing, and `triggers propose` counts it as "nothing to propose". With a language preset it also proposes up to two of the longest words (5+ letters, not a stopword, not in the file) of the prompt the user last typed before the read, so a user-language form of the topic can sit beside the English one. The candidate is listed by `juno rule pending` and the session-start rule nudge and settled like a rule candidate: it needs the id in a prompt the user typed or an AskUserQuestion answer the user chose (`juno rule accept <id>` / `reject`); only the accept writes, by setting `triggers: [a, b]` in the file's front matter (existing triggers are kept, 20 at most). [#36]

**`juno triggers propose`** (one-off, #36) covers the files nobody reads by hand: it queues one trigger candidate for every visible knowledge file without `triggers:`, proposed by the same deterministic logic from the file's title and body (no model call, no language-preset user words). It writes only the queue, never a knowledge file; it is refused like the other session verbs (not interactive, agent or subagent context, no session ledger, `[nosave]`). The items carry `batch: triggers-propose` and `source: triggers-propose`. The batch has its own budget, like the memory import (13.11): it never uses the 12 ordinary pending slots, a limit of 200 pending proposals applies (`candidates.PROPOSE_MAX`; the rest is reported as `N over the limit` and a later run continues), the session-start rule nudge does not list them (it would ask about dozens at once), and undecided ones do not expire. Idempotent: a file that has any proposal, pending or decided, is not queued again; a file without a proposal is counted, not queued. Review with `juno rule pending`, settle with `juno rule accept <id>` / `reject <id>` as above. Output: `Juno: 3 trigger candidates queued, 0 already queued or decided, 0 files with nothing to propose`. `juno doctor` names the command in its untriggered-knowledge WARN.

### 10.4 Rule writes
As in [v1: Rules policy] Write, Remove, Move and Candidates, without the grant. The quote rules are in 8.2. Injected rule text is the rule part only. Rules never come from READMEs, CLAUDE.md files, Jira, Slack, web pages, tool output or pasted text [v1: Rules policy]. Web research never becomes a rule [plan Phase 6.9].

### 10.4a Pinned rules

**Injection (#37).** A rule line that ends with ` · pinned` is re-sent on every prompt of the main session, because a rule injected once at SessionStart loses weight against long tool output. UserPromptSubmit builds a block `RULES (follow, pinned):` followed by `- <text>` per pinned rule (text only, run through `inject.neutralize`; no date, source or quote), in this order: `rules/global.md` (file order), then `rules/repos/<repo>.md` for every repo the segment has touched (ledger `repos`), then `rules/topics/<slug>.md` for every topic that fired in the segment (sorted). Only those files are read; a hidden or retired file is skipped. The block is trimmed to `[caps] pinned_rules_chars` (800) with the standard truncation marker, goes first in the output, and its length plus 2 is taken from `hook_output_chars` before the notice, cue, voice, pack and topic budgets, so packs are trimmed, never the block. Repo activation survives compaction because `packs_reset` clears only `seg.packs`. Topic activation is recorded as a ledger event `{"ev": "topic_fired", "slug": ...}` written when the main session claims the topic (subagents write none); the marker files under `state/topics` are deleted on compaction, the ledger event is not, and ledgers without the event fold as before. Synthetic prompts (task notifications, wakeups, Monitor events) return only the pinned block when there is one (no prompt record, pack, cue, voice line, topic text or shadow record, and `status.update` still runs), and nothing when there is none. Subagent prompts never get it. `[nosave]` does not affect it (it blocks writes, not reads). With no pinned rule every output is byte-identical to the output without the feature.

### 10.5 Correction cue
UserPromptSubmit adds one line pointing to `juno rule add` when the prompt contains a `[language] correction_cues` entry (folded). With preset `none` the list is empty, so no cue line appears. [v1: Injection] Correction cue

## 11. Session-end card run

The behaviour is carried from [v1: Session-end cards]. Changes are listed per subsection.

### 11.1 Run (`juno/hooks/card_run.py run <sid>`)
- Refuses without `JUNO_DISABLE=1`, a safe sid (`^[A-Za-z0-9_-]{1,128}$`) and the job token (`hmac.compare_digest`). One run per segment: `<sid>.lock` with a non-blocking flock.
- Re-folds the ledger. Skips when there is no stale card, or `[nosave]` is set, or the segment is private.
- Builds the digest as in [v1: Session-end cards] Run: typed prompts only; edits, commits, pushes, PR URLs; repos and tickets; the last assistant messages from the main chain; the "Learning moments" section; the last 12 Bash commands with head and tail; the current card. Caps come from the v1 `saga_cards.py` constants. Learning moments use `[language] user_cues` and `self_cues` (quotas user 4, self 2, fix 2).
- The prompt quotes the Format block of the vault's `core.md` "Handoff cards" section at run time. It fixes the heading values and fences the digest with a random nonce as untrusted data.
- The model call is `claude -p --model <model> --setting-sources "" --strict-mcp-config --disable-slash-commands --no-session-persistence --tools "" --system-prompt <...> --max-budget-usd <cap> --output-format json`. The prompt goes on stdin, stdin is closed afterwards (`</dev/null` semantics; [phase0 0.1] side finding), cwd is a fresh temp dir, and the process group is killed on timeout.
- The system prompt names the assistant Juno. It contains no user or company name.

### 11.2 Validation, per section
- The card is validated as in [v1: Session-end cards] Validation: one card, the exact heading with this seg8 and target key, `Repos:`, `Tickets:`, a non-empty `Learned:`, ≤ 16 lines, ≤ 2,500 chars, no secrets, no second person, link repair before validation. A card rejected only for `private data (id|phone|email)` is not dropped: the offending spans are replaced by `<id>`, `<phone>`, `<email>` (`privacy.mask`, which finds exactly what `privacy.personal` finds: URLs with an email, or a phone or id in the query or fragment, are masked whole; ticket keys, commit-hash contexts and other URLs are kept; the heading is not touched) and the card is validated again, with no extra model call. The counts go to `masked {kind: n}` in the `runs.jsonl` row and the job file; the rejected draft is stored nowhere. Secrets are never masked: the card is dropped. If the second validation fails, the card is dropped with that reason. The heading line is first rebuilt from the engine's date, key and seg8 around the model's label (`cardfmt.normalise_heading`); a heading with no label still fails.
- **Change from v1** [plan Phase 2]: each section is validated on its own. A bad card drops only the card, and the stub pass covers that segment later. A bad extraction kind drops only that kind. In v1, a card failure wrote nothing at all.
- **Decided here: an extraction kind is still written when the card fails, provided its evidence anchors in the digest.** Reason: the plan says one bad section drops only itself, and extraction evidence does not depend on the card text.

### 11.3 Target and write
As in [v1: Session-end cards] Target and Write. Routing follows section 4.2. `FILE: <slug>` is accepted only when the routing falls to `misc.md` and the slug passes the v1 rules. The card is replaced in place by seg8, or appended. A missing file is created with `# Handoffs · <name>`. The write goes through the same gate commit as the verbs (`gate.commit`, 14.2 step 5; the session write cap does not apply to the card run and stubs), so it is backed up, logged in `writes.jsonl` and undoable, and a symlink or an out-of-vault path is refused. Stubs use the same path. When the segment's job file (`cards/<sid>.job.json`) says `failed` with an `invalid: ...` error (`cardjob.card_failure`; a fixed string, 80 chars, scanned for privacy), the stub text reads "Card job failed (<reason>); Juno wrote this stub (...)" instead of "Segment ended without a card; ...", and the previous-segment line at the next SessionStart adds "its card job failed (<reason>) and the card was dropped". `juno card` writes only the current session's own card, and the line says it does not write the previous segment's.

### 11.4 Extraction
Each kind is one module in `juno/adapters/learnkinds/` (`KEY`, `instructions`, `state`, `apply`) listed in `KINDS`; `learnio` only runs the loop and the `sections` bookkeeping, so a new kind is one module and one tuple line.

One JSON block after the marker `---JUNO-EXTRACT---`. Kinds and limits are as in [v1: Session-end cards] Extraction:

| Kind | Considered / written per run | Target | Key limits |
|---|---|---|---|
| rule candidates | 6 / 3 queued, 12 pending total | `cards/candidates.json` (id `r`+6 hex) | rule 10-300, quote 20-300 verbatim folded; never writes `rules/` |
| knowledge | 6 / 3, 2 per file | `projects/<repo>.md` Gotchas (≤ 25; creates a minimal profile for a segment git repo) or `knowledge/<slug>.md` | text 20-300 neutral, evidence 4-200 anchored |
| threads | 1 open, 2 close | `threads.md` | 10-160 chars, ≤ 30 lines, ≤ 5 per tag |
| pitfalls | 4 / 2 | `pitfalls.md` | text 20-300, evidence anchored, 0-5 triggers |
| backlog | 3 | `cards/backlog-suggestions.json` (id `b`+6 hex) | log 10-200, status `done\|waiting\|doing`; ticket-done suggestion added by the engine [v1 backlog: 7] |
| keys | 2-5 facts, or 1 context sentence | `keys.jsonl` (state, never the vault) | facts 10-160 chars, the sentence at most 240; no `·`, passes the privacy and secret scan; a fact that fails is dropped alone and the kind is kept with at least 2 valid facts (or one valid context sentence when the model gave exactly one entry), at most the first 5 valid are kept, anything else drops the kind with its reason; after the card is written the engine re-splits the file and stores the facts under the hash of each passage of this segment's card heading |
| procedures | 1 / 1 | `procedures/<slug>.md` (engine gate write, verb `procedure`) | title 5-80, 2-12 steps of 5-200 chars each anchored in the digest, 0-5 triggers, repos limited to the segment's; the same slug is rewritten |
| ticket | 3 tickets / 20 lines each | `tasks/<KEY>.md` sections Summary, Criteria, Work, Tests, Cause through the `task log` code path (`taskio.put_rows`; engine write, verb `task-log`, one write id per ticket, so one `undo` each) | a line is `{section, key, text, evidence}`: keys `summary`; `ac1`, `ac2`, ...; the repo name (Work); `outcome` and `case-<id>` (Tests); `cause`; text 8-1000, evidence 4-200 anchored in the digest; a line that is unanchored, flagged by the privacy scan, holds a path outside the vault or a bare count (`5 ACs`, `1 of 3`) is dropped alone |

- The privacy checks are those of v1: no secret, email, phone number or 6+ digit id.
- **Ticket kind** [#33]. A ticket is written only when the segment worked on it: a ledger ticket with an edit on a branch naming it, a main-session tool call in a repo on such a branch or a tracker read of it (`main_tickets`), or a PR URL in the digest together with a typed prompt naming it; at most 3, best ranked first. **Decided here: a typed mention alone is not "working on" a ticket.** Reason: the issue lists a typed prompt as a signal but also says a key that is only mentioned writes nothing; the two only agree when a typed mention needs one more sign. A chat-only session also qualifies (criterion 10): the user typed the key in the segment and the ticket's block in the digest holds at least one assistant message that carries content. **Carries content** is a fixed rule with no model call: the message names the key (whole token, case-blind) and, with the key removed, has at least 12 words and 80 characters and holds one cue word of an approach, a criterion, a decision or a test result (`approach`, `plan`, `criteria`/`criterion`, `accept*`, `decid*`, `decision`, `cause`, `fix*`, `implement*`, `test*`, `passed`, `failed`, `blocked`). The `[language] preset` adds its own cue stems to the English ones, matched on the diacritic-folded text (so `yaklaşım` and `yaklasim` both match): `tr` adds `yaklasim`, `plan`, `kriter`, `kabul`, `karar`, `kok neden`, `neden`, `duzelt*`, `cozum`, `uygula*`, `test*`, `gecti`, `basarisiz`, `kaldi`, `bloke`; `none` adds nothing. A short note, or a long reply with no cue word, only mentions the ticket and writes nothing. A key the segment did not work on makes the item `dropped:unworked`. Sections are the fixed set above (created only when a line is written, registered in `digests:`); a created file has the frontmatter `task set` writes and `status` is never changed; user decisions stay with `juno decision`, so `## Decisions` is never written. A line that repeats a section and key replaces the old one in place, so a later segment replaces what changed and leaves the rest; the state block lists the ticket's current digest lines so the model can reuse keys. Instructions and the vault state omit the kind when no ticket qualifies or `[handoff] ticket_chars` is 0. The run record carries `ticket: {written: {KEY: lines}, dropped: {reason: count}}`; the reasons are `section`, `key`, `path`, `bare`, `invalid`, `no evidence`, `unanchored`, `private`, `unworked`.
- **Ticket block of the digest** [#33]. For each ticket the kind may write, the digest ends with `Ticket <KEY> (...)`: the main-chain assistant messages (never a sidechain) that name the key or follow a tool call whose arguments or command name it, newest first, redacted as the rest of the digest, within `[handoff] ticket_chars` shared by the tickets. It sits outside `digest_chars`, so the other sections keep their budget; the Stop hook's last message (`last_message_chars`) is not involved. What is cut away is not written.
- The redacted vault state is capped at 5,000 chars (pitfalls 1,500, backlog 800).
- The run record (`runs.jsonl`) carries the v1 fields plus `sections {card, rules, knowledge, threads, pitfalls, backlog, keys, procedures, ticket}`, each `ok|dropped:<reason>|none`, and `arms {name: on|off}` only while an arm runs (12.12).
- **Decided here: the `sections` field.** Reason: the per-section validation in 11.2 needs a record of which section dropped, for doctor and the Phase 6 signals.

### 11.5 Thread close candidates
After every run (ok, failed or skipped) the engine scans `threads.md`, as in [v1: Session-end cards] Thread close candidates:
- Ticket-done signals, only with `[jira]`.
- PR merged or closed: `gh pr view`, at most `gh_calls` calls, only when `gh_owner` is set, cached in `thread-prs.json`.
- Lines older than `[threads] stale_days`.
- The queue is capped at 12 pending ordinary candidates (the native-memory import batch, 13.11, has its own budget and is not counted). SessionStart nudges up to 4 of them. Nothing closes without the user's choice.

### 11.6 Approval nudges
SessionStart (not after compact) adds up to 4 rule candidates and up to 4 thread candidates as CONTEXT. Each comes with the instruction to ask once with one multiSelect AskUserQuestion (Recommended first, each label starting with its id), then to run `juno rule|thread accept|close …`. Backlog suggestions are shown only when the user asks for the list [v1 backlog: 2, 7].

### 11.7 Records and budget
`runs.jsonl` is both the run record and the daily cost ledger. The daily budget is the sum of today's `cost_usd` in local days. Doctor shows 7 days. A failed run leaves the segment to the stub pass. [v1: Session-end cards] Records

## 12. Other carried features

### 12.1 Search
Carried from [v1: Phase 2 item 1] and [v1: Phase 3] search noise fixes:
- The index is FTS5 `porter unicode61 remove_diacritics` in the state dir. It is synced on each call by a stat signature (size, mtime_ns, inode).
- Passages are heading-based, about 900 chars, at most 200 per file, one result per file.
- A ticket key in the query puts `tasks/<KEY>.md` and `handoffs/<KEY>.md` first (score 100), only with `[jira]`.
- URLs are dropped from query tokens. Stub passages never match. Repeated passages are dropped.
- Typo tolerance applies to the CLI only: one adjacent swap, insertion or deletion, `difflib` cutoff 0.75, at most 3, and the output shows `(searched "w" for "typo")`.
- `spread=True` applies to the CLI only: no kind takes more than ceil(limit/2) slots.
- Retired and private files are excluded. `archive/` is included only with `--archive`.
- The index is never deleted on BUSY. A corrupt file is moved aside to `.bad`. A foreign database is refused.
- Folding follows `[language] preset`.
- The `juno` skill runs the search as a bounded loop (2-4 searches with varied wording, Read of the top 1-3 files, Grep or Glob on the vault for exact identifiers, at most 6 tool calls, cite `path · heading`); `Grep` and `Glob` are allowed for reading only, and PreToolUse still refuses vault writes (14.4).
- **Fact keys** [phase6 6.1]: the index was schema 2 here (now 3, below), `passages(title, heading, body, keys, raw UNINDEXED, h UNINDEXED)`, and an old index is rebuilt at the next sync (a read-only open of schema 1 raises until then). `h` is the first 16 hex chars of `sha256(nfc(raw passage))`; `keys` is looked up by `h` in `keys.jsonl` (`{h, rel, facts, by: card|backfill, model, t}` plus `why: invalid|count|length|private|none` on a row with no facts, `dropped` (the number of facts discarded, never their text) when any were, and `retried: true` on a row stored by the retry; the last row of an `h` wins), so a key is content-addressed and an edited passage loses it. The facts are never written to the vault; results still show the raw passage text. When only `keys.jsonl` changed (meta `keys_sig`), a sync updates the `keys` column of the differing passages without reindexing any file. `[keys] weight` is the fourth BM25 weight; 0 also restricts matching to `title`, `heading` and `body`.
- `juno keys backfill [--limit N] [--max-usd 2.0] [--dry-run] [--json]` closes Phase 5.5: it keys the passages that have no row, skipping passages under 200 chars, stub passages, `rules/`, `protocol/`, `_generated/`, `core.md` and private files. It sends batches of 20 passages redacted as in 11.4, one `claude -p` call per batch with the flags of 11.1 and `[keys] model`, expects a JSON map `id -> facts` and validates the facts as in 11.4. Facts are validated one by one (11.4): a passage whose valid facts fall short is stored with no facts and the `why` of its first failing fact (`count` for an empty list, `none` on rows written before `why` existed). The next run retries every such row once more, in the same batches, and marks the row `retried: true` whether or not the retry produced facts; a retried row is never retried again. A batch the model does not answer in JSON is retried by the next run. It runs in the foreground, one progress line per batch on stderr, and stops at the first model failure or when `--max-usd` is spent; a second run resumes. There is no detach, because a background run dies with the session. `juno keys status` gives keyed, pending and empty passages, how many empty rows a run would retry, and the empty rows by `why`; doctor WARNs below 80% coverage while `[keys] weight` is above 0.
- **Time-aware search** [phase6 6.3]: the index is schema 3, `passages(..., h UNINDEXED, day UNINDEXED, sup UNINDEXED)`. `day` is the newest valid ISO date in the passage heading or body, else the file's `updated`, `valid_from` or `created`, else the date in a `notes/` file name, else the local mtime date. `sup` is set for passages under `## History` of a `decisions/` file. `juno search --since D --until D` keeps passages whose `day` lies in the inclusive range (a ticket-file hit is filtered too); results print `· <day>` and `[superseded]`, and `--json` carries `day` and `superseded`. A malformed date or `--since` after `--until` is refused (exit 2). The shadow path passes no range. `superseded_on` is not stored, because the `· superseded <day>` suffix and `supersedes:` already say it.

### 12.2 Packs and trimming
As in [v1: Injection] Packs: RULES first, then CONTEXT. The CONTEXT order is decisions, ticket digest sections, Gotchas, How to run, cards (1-2), ticket Notes, threads, knowledge titles, procedure titles (repo packs, by `repos`), then Linked when its arm is on. Purpose follows the cards only when `[packs] purpose = true` (default off, 6.6).
- Each pack is ≤ 4,500 chars, trimmed by section with the water-fill split (`core.trim_pack`).
- A card keeps its heading and priority lines. Other sections (Gotchas and the like) grow by appending, so an over-budget section keeps its newest items and drops the oldest, in display order [#19].
- Ranked trim [#20]: with a query (the last user prompt plus the triggering tool's file path, command or pattern; none at SessionStart or with an empty prompt and no tool input) an over-budget list section can instead keep its highest-scored items, still printed in display order with the same marker. An item scores the equal-weight sum of its min-max scaled relevance (Okapi BM25 of the item against the query, over the section's items; `core/rank.py`, no index, no model) and its min-max scaled recency (position). Without a query or any match the positional rule above applies unchanged. A use signal per line does not exist, so importance is not part of the score. Rollout is shadow first: the positional trim stays the output while the ranked choice (kept item indexes of both, no text) is appended to `shadow-trim.jsonl` (1 MiB, one rotation, only while `[shadow] enabled`). The ranked choice is the output when a running `ranked` arm assigns `on` to the segment, else when `[packs] ranked_trim = true`.
- The subagent block reserves up to half of `subagent_global_rules_chars` for global rules before topic rules; over its room, global rules shorten to their first sentence, then keep the newest. Doctor WARNs (`pack fit`) when a profile Gotchas or How to run section exceeds `pack_chars`, or global rules exceed `subagent_global_rules_chars`.
- After 4 full packs in a segment, further packs are one-line pointers.

### 12.3 Links
Wikilink parsing, Obsidian resolution, the `links.json` cache and the doctor's broken and ghost link lines are carried from [v1: Injection] Linked block and [v1: Phase 1 item 5]. The card run's link repair is carried. Functional links (Phase 6.7 [docs/phase6.md]):
- **Decisions block** [#31]: titled `Decisions · <owner path>` (the ticket file, a visible profile, else the first legacy file; never the stale mark). Its lines are the owner's `## Decisions` plus the visible legacy `decisions/<topic>.md` `## Current`, dropping only exact repeats (`owners.merged_decisions`, the function `migrate --decisions` also uses), so a pack is byte-identical before and after the move. A ticket pack with no task file, or one without a `title`, shows the hint to run `juno task set`.
- **Digest sections** [#31]: `digests: [Plan, QA]` in a task file lists the `## <Section>` bodies written by `task log`; the ticket pack injects only those, in that order, after Decisions and before Notes. Over budget, `trim_pack(..., reserve_first=True, priority=...)` keeps all Decisions lines unless that would leave the other sections less than 600 chars (or half the room, if smaller), then the newest Decisions lines fill `room - 600`; the digest sections then fill newest-written first (latest `· YYYY-MM-DD` among a section's lines; a tie goes to the section registered later in `digests:`), each taking up to 3/4 of what is left, and Notes and cards share the rest (a third of the remainder first when it is over 600 chars). The oldest section is trimmed or dropped first and a dropped section's heading is not printed. Display order stays the `digests:` order; a pack with no digest section trims as before. A digest line that does not fit its share is cut at a word boundary with ` …` (`trim.cut_words`), not dropped, whatever its length (task digest lines run up to 1000 characters, 14.1); the file keeps it whole [#33].
- The Linked block lists at most 5 entries and 500 chars of the anchor's out-links, then backlinks, minus `handoffs/` and `archive/` files, retired and private files, and the pack's own rules, doc and decisions files. While a `decisions/<x>.md` still has an owner, the link graph is read as `juno migrate --decisions` will leave it (`linksio.virtual_move`): each planned owner takes the links of its post-move text, a missing task file exists as the stub, and the legacy file is gone, so every Linked block, backlinks included, is the same before and after the move. It costs one scan of the map's keys when no legacy file has an owner. The order prefers `decisions/`, `knowledge/`, `procedures/`, `tasks/`, `projects/`, then the rest.
- **Bare link resolution** [#29]: a path link (`[[handoffs/alpha|...]]`) resolves to that file. A bare stem with several candidates takes the first by this order: `tasks/`, `projects/`, `decisions/`, `knowledge/`, `procedures/`, then every other directory by depth, path length and path. So `[[alpha]]` is the repo profile `projects/alpha.md`, not `handoffs/alpha.md`, and a ticket key is `tasks/<KEY>.md`. The order is Juno's own and is not verified against Obsidian; a path link is the unambiguous form. `juno doctor` reports the bare links whose stem has more than one candidate as an INFO line.
- The gate is the `linked` arm while it runs (assignment per segment, `armsio.arms_for`), else `[links] linked_block`.
- The ledger `pack` event carries `nb`, up to 5 filtered neighbour paths, in both arms and with the switch off, whenever the list is not empty. The metric `linked_read_share` joins `nb` with the vault Reads of the same segment's transcript (`evalio`) because `usage.jsonl` (6.5) only covers segments after it was merged; the counters prefer the `usage.jsonl` `read` rows of the segment and fall back to the transcript when it has none. The per-segment counters are kept in `arms/segments.jsonl` (12.12), so the metric does not depend on the ledger window.
- The pre-registration is `docs/prereg/linked.json`. No engine writer for ticket, repo or decision relations: cards, `project links` and `supersedes:` already provide them.

### 12.4 Threads
The line format, the extraction opens and closes, and the close candidates are as in 11.4 and 11.5. Doctor warns about lines older than `thread_stale_days`.

### 12.5 Backlog
Carried in full from the Saga v1 backlog design: the file format (3), the quote gate (4, with the quote always required), the CLI (5), the skill flow (6, with research through a read-only subagent), session-end suggestions (7) and the doctor lines (8). The skill text names no company tool. Subagents may run `backlog list|show` only.

### 12.6 Stale detection
Carried from [v1: Stale memory detection]:
- The baseline is `verified`, else `updated`, else mtime.
- Signals are age over `stale_days`, and code drift: a commit after the baseline touching a backticked path. Git limits are 1.5 s per call and a 10 s budget.
- The repo is resolved through `[repos] roots`.
- The `## Decisions` section of a profile is excluded from the scan and from the fixer: a path cited only there neither marks the file stale nor becomes a fix candidate. Knowledge files keep theirs [#31].
- Verdicts go to `stale.json`. Doctor and packs read the cache only, signature-checked. Hooks never run git for stale detection.
- `scan` measures drift against the checked-out HEAD and the working tree. That is a known limit; the fixer (12.6.1) does not inherit it.

#### 12.6.1 Weekly stale fixer
`stale --fix` checks the knowledge and profile lines that name a code path against the repo's fetched default branch (the local default branch when the repo has no `origin` remote, decision 65), and fixes or archives the lines the code contradicts. It is off unless `[stale] auto_fix = true`.
1. **Select.** Files under `knowledge/` and `projects/` that are not private, retired or archived, have at least one backticked path token and either a path that went missing or a commit to a path since the baseline (a missing path alone counts only when the file is older than `stale_days`). Sorted by drift commits, then age; at most `--limit` / `max_files`. Files with no path token (age only) are skipped with `no code anchor`, because there is nothing to check them against.
2. **Pin.** Per repo (resolved as in 12.6): when the repo has no `origin` remote (`git remote get-url origin` fails), no fetch runs and the first existing local `refs/heads/<name>` of `branch_order` is pinned by its commit sha (fetch field `no remote`; no such branch skips the repo as `no default branch`; decision 65). Otherwise `git fetch origin` (a failed fetch is tolerated only when `FETCH_HEAD` is under 7 days old), then the first existing `origin/<name>` of `branch_order`; when `origin/HEAD` points elsewhere and that branch's last commit is more than 180 days older than `origin/HEAD`'s, `origin/HEAD` wins. One sha per repo and run. The working tree is never read, and the checked-out branch counts only when it is one of the `branch_order` names, by its committed sha.
3. **Facts.** The engine computes, at the sha: whether each token's path exists (`git cat-file -e`), up to 20 commits to it since the baseline, and renames.
4. **Candidates.** Content lines (not headings, markers, frontmatter or fenced blocks, and not lines the secret scan would redact) that hold a drifted or missing token, at most `max_lines_per_file`.
5. **Model call.** `claude -p --restricted --tools=Read,Grep,Glob --disallowedTools=Bash,Edit,Write,MultiEdit,NotebookEdit,WebFetch,WebSearch,Task --permission-mode=dontAsk --setting-sources "" --strict-mcp-config`, `JUNO_DISABLE=1`, the prompt on stdin (flag values use `=`, because a variadic flag before the prompt swallows it), cwd a fresh temp dir holding `git archive <sha>` of the top-level directories of the tokens per repo (capped at 200 MB, then the token paths only). `--restricted` is what confines the file tools to that directory: without it `-p` reads any path (checked on claude 2.1.319, e2e case `ModelCannotReadOutsideItsDirectory`). The model sees no vault and has no write tool. The note lines are JSON-encoded inside a nonce-fenced block as untrusted data. The system prompt names Juno and nobody else.
6. **Answer.** One JSON object `{"lines":[{"line","was","verdict":"OK|WRONG|GONE|UNSURE","new","evidence":["repo:<name>@<sha>:<path>:<line>"],"why"}]}`.
7. **Validation before any write.** Unknown keys, duplicate lines, a line that is not a candidate or whose `was` differs from the line byte for byte, and a verdict outside the enum are rejected. Every verdict but UNSURE needs evidence: the repo must be one of the file's repos, the sha a prefix of the pinned sha, and the path a file at that sha with the line inside it (for GONE the evidence may instead name a path the engine found absent). WRONG: `new` (cut at its first ` · `, a final period dropped) must pass the secret and privacy scan, be one line of at most 300 characters, differ from the old text and keep a `difflib` ratio of at least 0.3 against it (a rewrite is rejected); the engine rebuilds the line as the bullet, the new text and ` · src: repo <name>@<sha8> · <date>`. GONE: every path token on the line is absent at the sha, with no rename, and none is unchecked. UNSURE and every rejected verdict are never written. More than `max_changes_per_file` changes, or more than half of a file's candidates changed, writes nothing in that file (`too many changes`). The run stops at `run_budget_usd` / `--max-usd` (`budget`) and after `max_changes_per_run` writes; two model failures in a row end it as `failed`.
8. **Write.** Identity `job:stale-weekly`: the run refuses unless `[stale] auto_fix`, vault schema 2 or newer, no `CLAUDECODE` / `CLAUDE_CODE_*` session variable, no `*AGENT_ID` and no `JUNO_DISABLE`; it takes the synthetic segment `job-stale-weekly-<run>` (`write.guard_job`); the interactive guard refuses any `job-` sid. Each file is re-read under `vault.lock` (10 s; a busy lock skips the file) and written only if every candidate line is unchanged. A change goes through `gate.commit(..., run_cap=False)` with `actor: job:stale-weekly`, `run: <rid>` and the first verified evidence: WRONG as verb `fix`, GONE through `forget.move` (verbs `forget` and `forget-archive`). A file whose candidates are all settled (OK, fixed or archived) and complete gets `verified: <today>` as verb `verify`. So every write has a backup, a `writes.jsonl` record and a ledger event, and `juno undo --run <rid>` reverts the run.
9. **Snapshot.** After the writes, `snapshot.take("juno stale-fix <date>: N fixed, M archived")` under the view lock. The snapshot stages the whole vault, so it also commits whatever else changed there. A signing failure (no agent under launchd) is recorded in the row and is not fatal; the next `juno snapshot` commits.
10. **`--job`.** A week with an `ok` row is a no-op (no row). Otherwise the run defers (`deferred`) while a session ledger changed within `busy_minutes` or a card run is active; the next hourly slot retries (13.9). A run where no repo could be fetched is `failed`, so the slots retry.
11. **Report.** `<state>/jobs/stale-weekly/runs.jsonl` (90 days): `{t, run, day, week, status ok|dry-run|deferred|refused|failed|budget, files_checked, fixed, archived, verified, unsure, rejected:[{file,line,reason}], skipped:[{file,reason}], writes:[ids], refs:{repo:sha8}, branches:{repo:ref used, e.g. `origin/develop` or `main`}, fetch, cost_usd, snapshot}`. SessionStart adds one line `Juno: stale fixer <date>: N lines fixed, M archived, K unsure (juno undo --run <rid>; juno stale for the rest)` once per run (a failed or refused run prints its reason once per reason). The dashboard "Stale memory" row appends the last fix and turns yellow when unsure > 0, the last run failed, or the schedule is installed and the last `ok` run is over 8 days old.
- **Decided here: `--dry-run` runs without `auto_fix` and inside a Claude session, but not in an agent context or with `JUNO_DISABLE=1`.** Reason: it writes only its run row, and it is the way to look at the fixer before turning it on. It does spend model budget.

### 12.7 Retirement, supersedes, privacy
As in [v1: Phase 2 item 2], with the protected list from 4.2. `retired.json` is written at sync, and hooks only read it. `visibility: private` files are excluded from search, recap, packs and digest.

### 12.8 Compact, digest, snapshot, profiles
As in [v1: Phase 2 items 3, 5, 6] and [v1: Enforcement] Digest:
- Compact moves cards beyond 20 per file to `archive/handoffs/YYYY-MM.md`.
- Snapshot commits only when the vault has its own `.git`. It strips `GIT_*`, sets `core.hooksPath=/dev/null` and turns off fsmonitor. **Change from v1** (user decision 2026-11-07, org rule: no unsigned commits): snapshot no longer turns off `commit.gpgSign` and uses the user's own git signing config. When signing fails, snapshot commits nothing and exits 2 with one line, `Juno: snapshot failed: commit signing failed (<git's first error line>)`. The default message is `juno snapshot <date>`. An e2e case asserts a signed commit (`git log --show-signature`) when the test HOME has signing configured, and the one-line failure when it is set up to fail.

### 12.9 Shadow logging and eval
Carried from [v1: Phase 3]:
- `shadow_record` is called from UserPromptSubmit, main session only, with a 150 ms budget. It keeps 200 prompt chars and logs `suggestions`, `delivered`, `carried` and `pasted`.
- The recall proxy, `eval sample|freeze|floor|gate|recall|agree` and the gate thresholds (Wilson ≥ 0.5, forbidden 0, ≥ 20 hard negatives, abstention ≥ 0.9) are carried.
- Eval judges the logged suggestions, never a rerun against today's index.
- **Change** [plan, Experiment protocol]: an entry carries `arms {name: on|off}` only while an arm runs (12.12). v1 logged `"arm":"cards"` even with the holdout off; old entries keep their `arm` string and the dashboard card panel still reads it.
- Shadow retention is 365 days.
- **Paired comparison instead of interleaving** [phase6 section 4]: the hook shows no list, so there is nothing to interleave. When `keys.jsonl` exists and `[keys] weight` is above 0, the shadow entry also logs `alt`, the weight-0 top 5 paths, beside `suggestions`. `juno eval compare --setting keys [--weights 0,1,2,4]` re-runs every shadow entry that has recall evidence against today's index once per weight (always including 0) and reports paired cells (only weight w hit, only weight 0 hit, both, neither) with an exact two-sided sign test. Both settings share today's index, so the leakage that makes absolute reruns invalid cancels in a paired comparison.
- The 2-arm, salted-hash, pre-registration protocol is 12.12.

### 12.10 Status files, UI, dashboard
- `status.json` and `status/<sid>.json` keep the v1 fields minus the grant and rule-grant fields [v1: Phase 1.5]. `ui/` is the v1 saga-ui mod renamed: band "juno │ …", env `JUNO_UI_ASCII`, `JUNO_UI_DEBUG`.
- The dashboard is carried from [v1: Dashboard] without the alarm and mentee panels, the alarm budget, `pending.json` auth rows, or the launchd job. It runs on demand: `juno dashboard [--open]`. The UI language follows `[language] preset` (`tr` keeps the v1 Turkish strings, otherwise English).
- **Decided here: no launchd or systemd job for the dashboard.** Reason: [plan Phase 5.2] unloads the v1 dashboard job and names no v2 replacement, and an on-demand page is portable to Linux.
- **Decided here: the dashboard language follows the preset.** Reason: v1's UI text was Turkish, which only fits one user.

### 12.11 Ideas, recap, tasks
Read-only views, as in v1. `tasks` and the overview's active tickets apply only with `[jira]`.

### 12.12 Experiment arms (Phase 6)
- **Arm protocol** [phase6 I-A]: at most `MAX_RUNNING = 2` arms run at once (a protocol constant, not a config key). A session is assigned per arm by `int(sha256(salt + sid)[:8], 16) / 2**32 < fraction` (`on`, else `off`), so the same sid always lands in the same cell, and two arms with different salts are independent.
- State `arms/<name>.json` holds `prereg` (the pre-registration), `salt` (16 random hex chars written at start), `started`, `sha256` of the pre-registration, `status running|stopped|decided` and `result`; `arms/log.jsonl` logs start, stop and decide. A name matches `^[a-z][a-z0-9-]{1,30}$` and is never reused. The arm also stores `feature` (`linked`, `next`, `self` or `ranked`; `arm start --feature`, default the name), and hooks look up the running arm of a feature, not a name, so a stopped feature is restarted under a new name (`self-2`) and the history stays. At most one arm runs per feature.
- **State after the arm** [#17]: with no running arm for `self`, the newest `self` arm decides: `stopped` (no decision) turns the block off unless `[self] inject = true` (default false); `decided` keeps the block on; no arm ever started leaves it on. `arm status` (text and `--json` `forced`) lists the feature and the arm that currently forces it.
- The pre-registration is a JSON object: `hypothesis`, `assignment {fraction}`, `unit`, `metric {primary, code}` (optional `secondary`, `guard`), `min_n` (units per arm), `max_days`, `stop_rule`, `decision`, optional `baseline`, `caveat`, `note`. `metric.code` names a function in the registry of `juno/core/armmetrics.py`; a metric returns `{n: {on, off}, stop_fired, ...}`.
- `juno arm start <name> --prereg F` validates the file, writes the salt and refuses a third running arm or a second running arm of the same feature. `status` lists the arms with live counts. `stop` ends assignment without a result. `decide` verifies the stored sha256, refuses before `min_n` per arm unless the metric reports the stop rule fired or `max_days` has passed, runs the metric and writes `result` once. `start` and `stop` use the write guard (interactive main session only); `eval ab` is an alias of `arm status`.
- Hooks read the running arms once per call (`adapters/armsio.py`, cached on the `arms/` directory mtime; a missing directory costs one `stat`) and add `arms {name: on|off}` to the ledger `start` event, shadow entries and `runs.jsonl` rows, only while an arm runs.
- **Linked arm data before #29**: data collected for the `linked` arm before the bare-link order change in version 1.0.11 (#29) measured a different Linked block, because bare repo links then resolved to the handoff file and were dropped, so it is not comparable with data after it.
- **Removed**: `packs.card_arm`, `[ab] card_holdout` and the card-only `eval ab` report. A config that still sets `card_holdout` gets the doctor WARN `[ab] card_holdout is ignored; delete the key from the config`.
- **Segment log** [phase6 I-A, 6.7]: ledgers live 7 days but an arm runs for weeks, so `arms/segments.jsonl` keeps one row per segment that ran while an arm ran (`t` = the ledger's mtime, `sid`, `arms`, `prompts`, `cues`, and `counters {arm: {...}}` with counts and booleans only; the `linked` counters are `nb_units` and `nb_read`). `armseg.sweep` writes it from the ledgers of segments quiet for `stub_quiet_seconds`, at the end of every card run (detached, so no hook pays for the transcript read) and at the start of `arm status` and `arm decide` (forced). It only runs while an arm is running. A row is appended again when its ledger grew after `t`, and a reader keeps the last row per `sid`. A metric uses a row first and falls back to ledgers and transcripts only for sids without one. `[retention] arm_segments_days` (365) prunes it with the other state. A later arm adds its own counters function to `armseg.COUNTERS`.
- **Self arm turn labels and readiness** [phase6 6.10]: while the `self` arm runs (both sides), the card run adds a numbered list of every assistant text turn of the segment to the vault state (first 160 characters each, redacted as the digest, newest 60 turns and 8000 characters kept, so the state may exceed `STATE_CHARS` by up to the list) and the model labels indexes: `"turns": {"proposals": [i...], "pushback": [i...]}`. The engine counts the distinct listed indexes, drops the section as invalid for an unknown index, a non-list or `pushback > proposals`, and writes `turns {proposals, pushback}` plus `turns_seen` and `turns_total` to `runs.jsonl`. Without the arm the instruction asks for two integers from the digest (same drop rule) and the off-arm prompt is unchanged. A resumed segment counts the maximum per field over its runs, because each run sees the whole transcript. A metric may return `ready` and `reason`; `decide` also needs `ready` (when present) besides `min_n`, unless the stop rule fired or `max_days` passed. `recommended_pick` is ready at 450 questions, 40 segments and 150 proposals per arm.

### 12.13 Writing voice (#34)

Juno learns how the user writes to other people from the user's own outgoing text, and uses it when Claude drafts text that goes out under the user's name. Nothing is shipped with fixed values except the base list of AI tells. Code: `core/voicecollect.py` (outward-tool match, posted text, authorship), `core/voicemeasure.py` (measures, profile, drafting block, check), `core/voicecues.py` (cue presets), `adapters/voiceio.py` (state file, hooks glue, `self/voice.md`).

- **Registers**: `voice` (default: the user's everyday style) and `formal` (the request asks for it; the user's own careful texts).
- **Collect** (PostToolUse, main session only). A tool call that matches `[voice] outward_tools` writes one ledger `voice` record: the posted text (redacted, 1,200 bytes), the `draft` shown before it (the last Stop message, 600 bytes, when the sample is not `user`), the register (the one the request named, else the target's default, below), the target kind (`chat`, `tracker`, `mail`, `review`, `code`) and the authorship `by`: `user` when the posted text occurs in a prompt typed since the last Stop, `claude` when it is the draft unchanged (the text, or at least half of the draft, or 90% word overlap), `edited` when a draft exists, the user typed since it and the text shares at least half its words with it, else `claude`. Only `user` and `edited` count towards the voice. A complaint (`[voice] critique_cues`) typed after a draft request in the segment writes a `voice_fix` record with the quote and the draft. A draft request (below), or a `formal_cues` / `voice_cues` word after one, writes `voice_ask {reg}`; `formal_cues` select `formal`. Nothing is written when the segment is `[nosave]` or private, for a subagent, or in a headless run (no hook runs there).
- **Session end** (before the card decision, in both handoff modes). The segment's records move into `voice.jsonl` (5.2), deduplicated by id; a `[nosave]` or private segment adds nothing and removes the rows it added earlier. When new samples came in and a register has at least `min_samples` counted samples, `self/voice.md` is rewritten (no model call): `visibility: private` frontmatter; per register the measures (length median and p90 in characters and words, multi-line share, lowercase start, final period, non-ASCII letters, bullets, bold, headers, code spans, links, emoji, openers and closers, semicolons, parentheses, dashes, colons), up to 8 verbatim examples (newest `user` samples that pass `privacy.scan`, contain no `@mention` or mid-sentence capitalised word, and no token, compared case-insensitively, that is a known name; skipped, never masked) and the correction lines with their quote and a draft excerpt. The file is private (hidden from packs, search and indexes) and written under the vault lock, not through a write verb.
- **Inject** (UserPromptSubmit, and PreToolUse on an outward tool when no request came first). The block is computed from `voice.jsonl` (the same function that writes the file) and holds, within `[voice] budget_chars`: the register line (the draft starts with one line naming it, so one word switches it), the register's measures and examples, the correction lines of the register (also below the threshold), the base list of AI tells and the two-voices rule. Below `min_samples` the measures and examples are left out.
- **Check** (PreToolUse on an outward tool, never blocks). The posted text is compared with the register's measures (once it has a profile) and the base list; one CONTEXT line lists what is off, for example `5x the median length, 3 bullets, 2 emoji`. Text the user typed in the segment is not checked.
- **Known names** [#34]: a sample is not an example when any of its words, casefolded, is in the set computed at each use from: the `@mention` handles and mid-sentence capitalised words (3+ letters) of `projects/*.md` and `handoffs/*.md` (read, never stored), of every sample text in `voice.jsonl`, and of the user's own prompts (the names directory `voice-names.json` in the state dir, mode 0600, at most 2,000 names, extended at session end except for a `[nosave]` or private segment); and `[privacy] names`. The set exists in memory and in the state dir only; it is never written to the vault, a pack, `self/voice.md` or the drafting block. Over-matching (a capitalised product word) only drops an example. **Decided here.** Reason: the issue names these sources but the vault has no people list, so the same capitalisation heuristic is applied to them.
- **One-word switch** [#34]: `[voice] voice_cues` select the `voice` register like `formal_cues` select `formal`, after a draft request in the segment (a `formal_cues` match wins when both match).
- **`formal` is one-shot** [#34, user decision 2026-11-17]: drafts are `voice` unless the user says otherwise. A `formal` choice applies to the draft request that carried the cue and to the outward post that answers it (checked as `formal`); once that post is collected the segment's register returns to `voice` (the fold sets it when a `voice` record is applied under `formal`), so a later post needs a new formal cue. A formal cue in a prompt without a draft request still switches nothing.
- **Default register per target** [#34, user decision 2026-11-17]: a register named in the request (`formal_cues`, `voice_cues`) always wins. When the request named none, PreToolUse decides from the target: an outward call that matches `[voice] formal_targets` (same syntax as `outward_tools`) is `formal`, any other is `voice`. Defaults: `gh issue create`, `gh issue comment` and `gh pr create` (so their `--body` and `--body-file`; the posted text of `--body-file <path>` is the file's text, read relative to the hook's `cwd`, at most 20,000 bytes; `-` reads nothing), tracker tools that create or update an issue, ticket or description, and tracker tools that add or create a comment. `gh pr comment`, `gh pr review` and chat send tools stay `voice`. The check, the drafting block (injected again when the target's register differs from the block already given) and the collected sample's `reg` all use that register. A draft request with no cue writes `voice_ask {reg: voice, auto: true}`; the fold keeps `auto` so the target still decides, and a formal that was named in the request is still one-shot (after the post the segment returns to `auto`, not to a named `voice`). A tracker MCP name that also looks like a review comment matches the same patterns; narrow `formal_targets` for such a tool. Issue comments are formal like tracker comments (user decision, #34); PR comments stay voice. **Outward patterns match the command's own tokens** (`bashview.outward_view`: heredoc bodies dropped, quoted prose with spaces becomes `_`), so a `cat > f <<'EOF'` that only mentions `gh pr comment` runs no check; the posted text is still read from the raw `--body` argument. **Draft request: the verb phrase and its target are separate tokens** (a target token inside the verb's own tokens never counts, so `cevap ver` / `yorum yap` are not their own target); the Turkish list has no habitual `cevap verir`, and adds `paylaş` as a verb and `comment` as a target.
- **Draft request** [#34]: cue words alone (a register name) are not a request. A prompt is one only when it has a draft verb (write, draft, post, send, reply, respond, answer, comment on; `tr`: yaz, hazırla, gönder, at, cevap ver, yorum yap, in imperative and "-ar mısın / -ebilir misin / -sana" forms) within five words of a target (message, comment, issue, PR, mail, note, him/her/them, this; `tr`: mesaj, yorum, issue, taslak, ona, bunu, ...; suffixes after a target of 4+ letters are tolerated), or a `draft_cues` phrase. A **standing preference** is never a request: `standing_cues` (always, from now on, whenever, never; `tr`: her zaman, hep, genelde, bundan sonra, and the third-person optatives hazırlasın, yazsın, göndersin, atsın, cevap versin) anywhere in the prompt reject it, and also stop a register word from switching the segment. Example: "her zaman rahat bir dille taslak hazırlasın ben formal olduğunu belirtirim bir şekilde" says how to draft in general: it has a standing marker and an optative verb, and no text to write now. **Trade-off:** the rule is a deterministic word-list, not understanding. A real request that carries a standing word ("her zaman yaptığın gibi bir yorum yaz") is missed, and a verb form outside the lists is missed; the user can name the target plainly. The draft request itself still injects at prompt time before the target is known, so it carries the `voice` block; a formal target gets the `formal` block at its PreToolUse.
- **Two voices never mix.** Juno's own character applies only to replies to the user. The drafting block states that text sent under the user's name uses the register and carries no trace of Juno's character, and that a skill with its own document format keeps its format but still avoids the tells. The output-style text states the same rule (issue #35).
- **Import** [#38]: `juno voice import [--register voice|formal] [--target chat|mail|tracker|review|code] [--dry-run] [--json] <file | ->` adds the user's own past messages (written outside Claude, in a chat app, a mail client, a tracker or a review UI). The input is JSONL (every non-empty line a JSON object with a string `text`, plus optional `register`, `target`, `t` as ISO 8601 such as `2026-11-10T09:12:00Z` or epoch seconds) or plain text, one message per blank-line-separated paragraph; `-` reads stdin (20 MB at most). Row values override the flags. A row without a register gets the flag, else the target's default (`tracker`, `mail`, `code` are `formal`; `chat`, `review` are `voice`), else `voice`. **Decided here:** the default is a fixed target map, because `[voice] formal_targets` holds tool-name patterns, not target kinds. Each accepted message is a `sample` row with `by: "import"`, `sid: "import:<batch id>"`, `t` the send time (never later than now) or the import time, `kind` the target (`chat` when none), and the text redacted and cut to 1,200 bytes like a collected sample. `import` counts like `user` (`COUNTED`) and its texts are examples under the same rules as `user` texts (privacy scan, no `@mention` or mid-sentence capital, no known name); the rows are ordered by `t` for the newest-first pick. Skipped, never masked, with a count per register and reason: `quoted` (more than half of the lines start with `>`, or a forwarded-message header: `---------- Forwarded message`, `Begin forwarded message:`, `-----Original Message-----`, `İletilen mesaj`), `short` (under `MIN_CHARS`), `privacy` (`privacy.scan` on the original text), `duplicate` (the same stored text as an existing sample or an earlier message of the batch, so a repeat run adds nothing), `too_old` (older than `[voice] import_max_age_days`), `over_limit` (the newest `[voice] import_max` messages by `t` are taken). The batch id is the first 8 hex digits of a digest of the accepted texts, so a dry run and the real run print the same id. Names found in the accepted texts are added to `voice-names.json` like names from prompts. **Pruning:** `pruned` keeps the newest 400 live samples and, apart from them, the newest `import_max` imported samples, so an import never pushes out a live sample; session end uses the same rule. **Profile:** a real import that adds rows writes the state file and rebuilds `self/voice.md` with `write_profile` under the vault lock (`write.locked`; `held=True` tells `write_profile` not to take the lock again), without a session end. `--dry-run` reads only: `voice.jsonl`, `voice-names.json` and `self/voice.md` stay byte-identical and the report has the same numbers a real run would print. **Report** (text, or `--json`): `batch`, `dry_run`, `accepted`, `skipped`, `profile` (a profile is, or in a dry run would be, written), and per register (`voice`, `formal`): `accepted`, `skipped` (counts for `privacy`, `short`, `quoted`, `duplicate`, `too_old`, `over_limit`), `skipped_total`, `counted` (counted samples of the register after the import) and `ready`. **Forget:** `juno voice forget --batch <id> [--json]` removes the rows of that batch (`{batch, removed, profile}`), rebuilds `self/voice.md`, or removes it when no register is ready any more. `juno undo` does not apply (state, not vault). **Status:** `juno voice status [--json]` prints per register `user`, `edited`, `import`, `counted`, `min_samples` and `ready` (JSON: `{enabled, min_samples, registers: {voice: {...}, formal: {...}}}`). **Refusals:** `import` and `forget` refuse in a subagent, in a `[nosave]` or private segment of the calling session (`CLAUDE_CODE_SESSION_ID` with a ledger), and when `[voice] enabled` is false. **Decided here:** with no session (a plain shell), no flag hides anything and the verbs run, because `voice.jsonl` is state, not vault; `status` reads only and is never refused. **Privacy:** the text goes only to `voice.jsonl` (0600) and the examples of `self/voice.md`; the verbs write no ledger record, pack, card or vault note, and the report holds counts only. A caller should pass the messages through a file, because a Bash call's command line can be recorded in the segment ledger. **Doctor:** with `[voice] enabled`, no register ready and at least `[thresholds] meaningful_user_turns` × 10 user turns (the `prompt` records with `part` 0 over all session ledgers, counted until the number is reached; synthetic prompts are not told apart), `juno doctor` prints `INFO voice: ...` suggesting `juno voice import`.
- **Decided here: the profile is computed from `voice.jsonl` at injection, and `self/voice.md` is the readable copy rebuilt at session end.** Reason: the check needs numbers, and parsing a prose file for them would be fragile.
- **Decided here: correction lines are injected below the sample threshold too.** Reason: they are the user's own words about a draft, not a measure, and the threshold only guards the statistics.
- **Decided here: authorship uses the last Stop message as the draft.** Reason: the hooks see no other text the user saw before the post.

### 12.14 Selftest (own questions against the search)

`juno selftest` measures with the user's own questions whether `juno search` finds the right note (idea from upstream avenoxbeyin #675, #860). Files: `juno/cmd/selftest.py` (verbs), `juno/adapters/selftestio.py` (state file, lock, vault path check, the search call), `juno/core/selftest.py` (row, path syntax, scoring); tests in `tests/test_selftest.py`.

- **Storage:** `selftest.json` in the state dir, mode 0600, written atomically under `selftest.lock`; never in the vault.
- `add "<question>" --expect <path> [--sentence "<phrase>"]` saves a question; the path must be a vault-relative `.md` file that exists, with no `..`, `.` or empty part and no symlink on the way (an absolute path is accepted only inside the vault and stored relative). `add "<question>" --control` saves a control. **Decided here:** a control has no expected file and passes when `juno search` returns no result at all for it (same function and filters as `juno search`, so the normal threshold applies); it fails when anything is returned. `--sentence` does not go with `--control`. `list [--json]` and `remove <id>` (ids are `q1`, `q2`, ..., never reused).
- `run [--limit K] [--budget CHARS] [--json]` asks every question through the retrieval `juno search <q> --limit K` uses (default 5, no repo, ticket, date or archive filter) and reports per question the `rank` of the expected file (`null` is a miss), `hit1`, `hitk`, `returned`, and for a question with a sentence `sentence`: `found`, `cut_by_budget` or `absent`. A sentence is checked against the snippets of the expected file's returned hits, case and whitespace folded. With `--budget`, the snippets in rank order, one newline between them, are cut at CHARS; a sentence present in the returned text but not in the cut text is `cut_by_budget` (file found, sentence cut), kept apart from `absent`. The summary holds hit@1 and hit@K counts and rates over the non-control questions, the Wilson lower bound of hit@K (`juno.core.eval.wilson_lower`), the sentence counts and `controls` / `controls_passed`. `run` always exits 0.
- **Refusals:** `add` and `remove` refuse in a subagent and in a `[nosave]` or private segment of the calling session, as `voice import` does (a plain shell runs them); `list` and `run` are never refused.

## 13. Install and lifecycle

### 13.1 `juno init`
Asks nothing [plan], except three questions at the end of an interactive run (steps 8, 9 and 10). It is idempotent, and each step prints one line.
1. Interpreter discovery (13.2). Refuses below 3.14 with one line and exit 2.
2. Config: creates `juno.toml` when missing, with `[runtime]` first. Otherwise it only adds or updates `[runtime] python` and `plugin_root`, and never changes another key.
3. State dir: created with mode 0700.
4. Vault: when the vault is missing, it is copied from `vault-template/` and `git init` is run in it. An existing vault is never overwritten. When the existing vault's `juno-brain.toml` schema is older than the code's, init stops and points to `juno migrate`.
5. Settings merge (13.4).
6. Shim (13.3).
7. Plugin: init checks whether `juno@<marketplace>` is installed and enabled. When it is not, init prints the `claude plugin install` command and exits 0. **Decided here: init does not install the plugin itself.** Reason: plugin installation belongs to Claude Code's own CLI and trust prompts.
8. Stale fixer question (user decision, 2026-11-08): only when stdin and stdout are terminals, not with `--dry-run`, and only if `[stale] auto_fix` is not already set in the config and the question was not asked before (marker `stale-fix-asked` in the state dir). `Fix stale notes automatically every Sunday? (model calls, at most $3 a week) [y/N]`, default No. Yes writes `[stale] auto_fix = true` to the config and runs the equivalent of `juno schedule install stale-weekly` (13.9); a refusal there is printed and init still exits 0. A non-interactive init asks nothing and leaves the fixer off.
9. Output style question (decision 69, 13.10): same conditions as step 8, own marker `output-style-asked`. One choice question for the humour dose.
10. Native memory import question (13.11): only when Claude's own memory has importable notes. Same conditions as step 8, own marker `memory-import-asked`. `Claude's own memory has 23 notes in 4 projects. Import them into Juno? [Y/n] `, default Yes. No, EOF or a non-interactive run prints `juno import-memory` as the later command. With `--dry-run` init only reports that it would ask. This is why `autoMemoryEnabled: false` (13.4) does not lose what Claude remembered before Juno.

The first run before the shim exists is `"<plugin root>/bin/juno" init`. The SessionStart launcher prints this command when there is no config (6.1).

Done when: init and the plugin install on a clean user account give a working SessionStart injection, and uninstall leaves the settings byte-identical [plan Phase 1].

### 13.2 Interpreter discovery
The order is Homebrew, then pyenv, then uv, then system [phase0 0.2]:
- Homebrew: `/opt/homebrew/bin/python3*`, `/usr/local/bin/python3*`, `/home/linuxbrew/.linuxbrew/bin/python3*`.
- pyenv: `$(pyenv root)/versions/*/bin/python3`.
- uv: `uv python list --only-installed`, using the path column.
- system: `/usr/bin/python3`, then `python3` on PATH.

The candidates are deduplicated by realpath, because uv lists other managers' interpreters [phase0 0.2]. Each one is probed with `<py> -I -S -c "import sys; print(sys.version_info[:2])"` (2 s timeout). The first source with a version ≥ 3.14 wins, and within a source the highest version wins. The resolved path is stored in `[runtime] python`, so `hooks.json` stays static [plan]. `JUNO_PYTHON` overrides discovery for tests and the launcher. Doctor re-probes the cached path and FAILs below 3.14 or when it is missing.

### 13.3 Shim `~/.local/bin/juno`
A POSIX sh script written by init. It is refused when a non-Juno file already exists at that path.
- It reads `[runtime] python` and `plugin_root` with the same `sed` as the launcher, and runs `"$py" -I -S "$root/juno/cli.py" "$@"`.
- When `plugin_root` lacks `juno/cli.py` (a plugin update moved it), it falls back to the newest install path for `juno` in `~/.claude/plugins/installed_plugins.json`. When that fails too, it prints `Juno: plugin not found; run juno init` and exits 2.
- SessionStart rewrites the `plugin_root` line atomically when `CLAUDE_PLUGIN_ROOT` differs. This is the only config write outside `init`, `import` and `migrate`.
- **Decided here: SessionStart refresh plus the `installed_plugins.json` fallback.** Reason: `CLAUDE_PLUGIN_ROOT` changes per plugin version [phase0 0.1]. [phase0 0.4] found a stale install record, so that record is only a fallback.

### 13.4 Settings merge
- Carried from v1 `install.py` and [v1: Decisions] Installer: adds only, never removes or reorders; refuses non-strict JSON; backs up the original to `install-backups/`; records every added entry in `install-manifest.json`; verifies after writing.
- Target: `~/.claude/settings.json`.
- Settings writes go through `fs.write_settings`: a symlinked file stays a symlink (the resolved target is written), a hardlinked file is rewritten in place, and init and uninstall stamp the file (inode, mtime_ns, size) when they read it and write nothing if it changed before the write. Settings JSON is read with a UTF-8 BOM allowed. `fs.atomic_write`, used for vault and state, still does not follow symlinks.

| Key | Added |
|---|---|
| `permissions.additionalDirectories` | absolute vault path |
| `permissions.allow` | `Read(<vault>/**)`, `Edit(<vault>/**)` (`~`-relative when under home, as in v1) |
| `permissions.deny` | `Edit(<vault>/rules/**)`, `Edit(<vault>/.git/**)` (8.4) |
| `autoMemoryEnabled` | `false`; the manifest records the previous value or its absence |

- Hooks are **not** merged, because the plugin provides them [plan, phase0 0.1].
- `--dry-run` prints the diff. **Decided here: init applies by default and `--dry-run` previews.** Reason: the plan says init "asks nothing". v1's dry-run default needed a second command.

### 13.5 `juno uninstall`
- When the settings file is byte-identical to the post-install bytes recorded in the manifest, it restores the original bytes. Otherwise it removes only the recorded entries and restores `autoMemoryEnabled` to its recorded previous state.
- When init set `outputStyle` (13.10), the manifest record keeps it and uninstall removes it again unless the user changed it since.
- It removes the shim only when the shim is Juno's.
- It never touches the vault or the state dir, apart from deleting the manifest last. [plan, v1 uninstall]

### 13.6 `juno doctor`
- Carried checks [v1: Phase 1 item 5], [v1: Phase 2 item 7], [v1: Decisions] Doctor privacy, [v1: Stale memory detection] item 4, [v1 backlog: 8]:
  - hook errors and timings (p50 and p95 per event, cancelled runs);
  - card headings and fields;
  - rule provenance and caps;
  - core and global budgets (FAIL over the cap, WARN at ≥ 90%);
  - repos without a profile (WARN only for repos named in at least `profile_min_cards` valid cards, a segment counted once, most used first with counts; the rest as one INFO count);
  - pending stubs;
  - dead and retired supersedes;
  - handoff files over 20 cards;
  - stale learned;
  - index readability;
  - broken wikilinks;
  - `decisions`: WARN for each `decisions/<x>.md` whose owner file exists (names `juno migrate --decisions`), INFO for files that stay with a reason;
  - privacy patterns;
  - duplicate segment cards;
  - card runs and learning counts;
  - stale cache;
  - backlog counts;
  - pitfall lines;
  - shadow entries per day.
- New checks:
  - interpreter ≥ 3.14 and both config readers agree (13.2, 6.1);
  - `plugin_root` valid and plugin enabled;
  - shim present and resolving;
  - manifest entries still present in the settings;
  - `autoMemoryEnabled` false;
  - the output style (13.10): PASS when `outputStyle` in the user settings is one of `juno:juno-calm`, `juno:juno-normal`, `juno:juno-playful` (the line shows which), INFO when it is not, WARN when it is a retired value (naming `juno migrate --style`) or when the selected style file (`juno-calm.md` when none is selected) is missing from the plugin;
  - config types valid;
  - vault schema version matches the code;
  - `hook_output_chars` < 10,000.
- Removed checks: grants, alarms, mentees, guard probes. The PreToolUse probe stays as a latency check: median < 50 ms in-process.
- Exit 1 on any FAIL. `--json` gives the collecting, read-only mode the dashboard uses.

### 13.7 `juno export` / `juno import`
- `export [--out PATH] [--with-state]` writes a tarball (mode 0600) of the vault, including its `.git`, and the config. The `[runtime]` table is omitted. `--with-state` adds `shadow.jsonl`, `eval/` and the `cards/*.json` queues. [plan]
- **Decided here: the `--with-state` option.** Reason: shadow data and pending candidates are not derivable and would otherwise be lost on a move. The plan's purpose is "disk loss is otherwise total".
- `import PATH` refuses when the target vault exists and is non-empty. It unpacks with path checks (no absolute paths, no `..`, no symlinks out), writes the config without `[runtime]`, then runs `init`.
- Never copy a vault between people [plan, docs/colleague.md].

### 13.8 `juno migrate`
- `migrate [--dry-run]` reads `juno-brain.toml` `schema_version` and applies one version step at a time. Each step backs up the vault, writes the new version last, and is idempotent. [plan]
- History: the `--from-saga` step, `rollback` and the cutover contract were removed by decision 61.
- Step 1 to 2 (`adapters/migratesteps.py`): in the `core.md` Privacy invariant block, the exact sentence `Write memory only in this interactive session with me, never from a subagent.` gets the stale fixer exception appended (12.6.1, decision 63). It is idempotent, and a block the user edited (no such sentence) is left alone; doctor then WARNs while `[stale] auto_fix` is on and the block does not mention the stale fixer. Fresh vaults come from the template already at the current schema.
- Step 2 to 3: in `protocol/rules.md` the exact quote-rule line (the one that named the chosen option's "label or description" as a quote source) is replaced by the line that allows only typed text, the chosen option's label and the Other text or notes (8.2, decision 68); in `core.md` the exact sentence `Stay under the per-file and per-session caps.` becomes `Stay under the per-session cap.` (decision 64). `core.md` and `protocol/` are not covered by `juno fix`, hence the step. Each replacement is idempotent, and a line the user edited (no exact old text) is left alone without an error.
- Step 3 to 4: ticket decisions have one home, `decisions/<KEY>.md` `## Current`, with the key in uppercase (`ABC-1`). A decision file whose name is a ticket key in another case is renamed to the uppercase name (merged into it when both exist; duplicates and near-duplicates are skipped or updated, `## Current` and `## History` kept apart). The lines of `tasks/<KEY>.md` `## Decisions` move into `decisions/<KEY>.md` `## Current` unchanged (source and date stay in each line), the same near-duplicate rule applies, and the section is dropped from the task file. A new task file no longer gets a `## Decisions` heading. Idempotent. Reason: the pack read only the decision file, so the older task-file lines were never injected, and a lowercase topic file was found only on a case-insensitive filesystem. Until a vault is migrated, the ticket pack also shows the lines of `tasks/<KEY>.md` `## Decisions`.
- Step 4 to 5: the card format in `core.md` showed `Links:` entries without a reason, though a card link needs one (`[[target]] (reason)`). Two exact replacements, each independent of the other (an older `core.md` may lack the `--links` fragment): the format line `Links: [[<repo>]], [[<KEY>]], ...` becomes `Links: [[<repo>]] (why), ...`, and the `juno card` fragment `[--links "[[<repo>]]"]` becomes `[--links "[[<repo>]] (why)"]`. Idempotent; a line the user edited is left alone without an error. Protocol files (`links.md`, `handoffs.md`) are synced by `juno migrate --protocol`.
- Step 5 to 6 (#31): every `decisions/<x>.md` that a visible profile (raw topic name, case-exact) or a configured ticket prefix (`tasks/<KEY>.md`, created as a stub with the file's mtime when missing) owns moves into the owner's `## Decisions`: owner lines first, then the legacy `## Current` lines, dropping exact repeats only; `## History` goes unchanged to `archive/decisions/<topic>.md`. Order: archive, owner, read both back, then delete the source; backup first; idempotent; an existing owner keeps its mtime and its stale-cache signature is renewed so a `(may be stale since ...)` title survives. A decision file with no `## Current` line whose owner file does not exist makes no owner (no empty task file): its `## History` lines go to the archive as usual and the source is removed, reported as `decisions: removed decisions/X.md (no current lines[, N to archive/decisions/X.md])`; the skip rules below apply first, and an existing owner is handled as above. The pack pointer does not name a ticket file that holds nothing (no `title`, no non-blank line under any heading), which also covers the stubs older moves left; they are not deleted. Files that stay, reported with the reason: hidden files, files whose owner is hidden, files with text outside `## Current` and `## History`, and topics without an owner. `[[decisions/x]]` path links are counted, not rewritten. Privacy acknowledgements (`.privacy-ack.jsonl`) of a moved file follow it (see the next item). Old Juno rejects a schema 6 vault.
- `migrate --decisions [--dry-run]` (no schema bump): the same move on its own, for a profile that appeared after step 5 to 6; a second run moves nothing. Privacy acknowledgements: every record whose `file` is `decisions/<x>.md`, that no longer exists, whose owner exists and whose owner text holds the acknowledged span (by hash) is rewritten to the owner's path and the span's new line (backup of the ack file, atomic write, no write id: the migration convention, not the write gate), so it also repairs a vault that was moved before this existed and runs when there is nothing to move; `--dry-run` only reports. Output `decisions: N privacy acknowledgements moved with their lines` (`would move` with `--dry-run`) and, for records whose span is not in the owner (a `## History` line went to `archive/decisions/`, which doctor does not scan), `decisions: M privacy acknowledgements stay (...)`. An ack stays bound to one file; doctor never matches by hash across files.
- The code is at schema 6, so doctor FAILs and init stops at an older vault until `juno migrate` ran.
- `migrate --cards [--dry-run]` (no schema bump): older vaults hold the same card in several handoff files. For a segment filed in more than one file, every copy outside the files the card names (a repo of its `Repos:` line or its ticket key) is removed (backup first); a card filed once in each repo file it names (a ticketless card written per repo) keeps all of them and is not warned about. When no copy is in a named file, one stays: `handoffs/misc.md` if the card is there, else the first file by name (file age is not recorded); a lone misfiled copy is then moved as below. The dry run lists each file and segment it would drop. A card left in a repo's file it does not name (what `doctor` calls misfiled) is then moved to the handoff file of the first repo its `Repos:` line names, created if needed, placed by the card's time stamp; the destination is written and read back before the source loses the card, and a destination that already holds the segment makes it a plain copy drop. The output lists what moved. Idempotent. `doctor` WARNs `card copies` (a segment with a copy `migrate --cards` would drop, by the same rule) and `misfiled cards` (a card in a repo's file it does not name). `packs.all_cards` shows a segment once, preferring the copy filed under a repo or ticket it names, which also dedupes `_generated/learned.md`.
- `migrate --notes [--dry-run]` (no schema bump): a `notes/*.md` file with a frontmatter and no non-blank body line (what `juno forget` left before it removed the file with the last line, issue #27) is moved to `archive/decayed/forgotten-YYYY-MM.md` as `(no body line) · archived <day> · from <file> · note frontmatter: <fields>` (backup of both files first). Prints `notes: archived <file> ...` or `would archive` with `--dry-run`, `notes: no body-less notes` when there is none; idempotent. `doctor` WARNs `note bodies` per such file, naming this command. Other note files are never touched.
- `migrate --protocol [--dry-run]` (no schema bump): `protocol/*.md` in the vault is copied once by `init` and never updated, so it drifts when the plugin's `vault-template/protocol/` changes. `core/protocolsync.py` keeps `SHIPPED`, the sha256 (line endings and trailing blank lines normalised) of every template version ever shipped. A vault file equal to the current template is current; equal to an older shipped hash it was never edited and is replaced (the old copy goes to `state/backups/` first); any other content is user-edited and only reported, never overwritten; a missing file is restored; a symlink is left alone. `--dry-run` lists what would change. `doctor` reports each file (`protocol` checks). When a template changes, append its new hash to `SHIPPED` (a test fails otherwise) and never remove old ones.
- `thread list --stale [--days N]` (default 7) prints the open threads whose `since` is that old; `doctor` adds `threads: N open, M older than 7 days (juno thread list --stale)`. Nothing closes a thread automatically; `thread close --line N --was T --evidence <user's words>` does, undoable. A thread opened with a same-tag near-duplicate (70%+ shared meaningful words, 6+ words) updates the existing line and keeps its `since`.

### 13.9 `juno schedule`
- `schedule install|remove|status [stale-weekly]`, action first like `juno rule add`; the job name is for future jobs. `install` refuses unless `[stale] auto_fix = true` and the vault is at schema 2, and rejects an invalid `weekday` or hour window. `init` offers it once (13.1 step 8) and never installs it otherwise.
- macOS: `~/Library/LaunchAgents/dev.juno.stale-weekly.plist`, built with `plistlib`: `ProgramArguments` `[<[runtime] python>, -I, -S, <plugin_root>/juno/cli.py, stale, --fix, --job]`, `StartCalendarInterval` one entry per hour from `start_hour` to `end_hour` on `weekday` (so a deferred or missed run retries and launchd fires a missed slot after wake), `EnvironmentVariables` `HOME`, `PATH` from `[handoff] launch_path`, and `JUNO_CONFIG`, `JUNO_VAULT`, `JUNO_STATE`, `JUNO_REPO_ROOTS` when set at install time, stdout and stderr to `<state>/jobs/stale-weekly/stale-weekly.log`, `RunAtLoad` false. Loaded with `launchctl bootstrap gui/<uid>` after a `bootout`.
- Linux: `~/.config/systemd/user/juno-stale-weekly.service` and `.timer` (`OnCalendar=<Day> *-*-* HH..HH:00:00`, `Persistent=true`), enabled with `systemctl --user enable --now`. Without `systemctl` it prints the crontab line and writes nothing.
- No path of the machine is in the code: every path is computed at install time from `HOME`, the config and the plugin root. Tests point `JUNO_LAUNCHCTL`, `JUNO_SYSTEMCTL` and `JUNO_SCHEDULE_PLATFORM` at fakes, and never run the real tools.
- `status` shows installed, loaded, `auto_fix`, the last run row and the next fire time; `remove` unloads and deletes the files.
- Signed snapshots under launchd need a signing key the job can reach (an ssh-agent or gpg-agent socket in the job's environment); otherwise the row records the failure.

### 13.10 Output style (decision 69; decisions 66 and 67 are superseded, see the history under 17)
- `output-styles/juno-calm.md`, `juno-normal.md` and `juno-playful.md` give the assistant one character, the same for every user and in every language, plus a humour dose. The three files share one identical character block (byte-identical, enforced by a test) and differ only in the last block, `Humour dose: calm|normal|playful`, and in the frontmatter `name` and `description`. The character: a close friend who is also a sharp colleague, funny, teasing and joking; answer first, then only the one to three lines of why that change what the user does; says plainly when something is wrong and why, offers the better option, then does what the user decides; asks rarely, never about a reversible choice that has a recommendation, and puts a real decision through the structured question tool with the recommended option first and marked; teases the user and itself, never other people or their code; names its own mistake plainly with one dig at itself, then the fix; normal spelling in the user's language, loose (lowercase starts, no final period needed); emoji where they fit; the first reply of a session greets like a friend and names one relevant unfinished item from the injected context in a line (never invents one), then the request. Unchanged rules: name a contradicted earlier decision, keep memory, checked and inferred apart, never claim done without evidence, own unfinished work, user instructions (CLAUDE.md, rules, language) first, say it runs on Claude when asked.
- Serious moments (production errors, incidents, bad news) have no jokes, no teasing and no emoji at any dose: calm first, then the facts and the next step.
- Two voices: the character applies only to Juno's replies to the user. Text drafted to be sent under the user's name uses the user's own voice and register (voice collection, issue #34, injects it), and a document made by a skill keeps its own format; no joke, teasing or emoji of the character appears in either.
- Doses: `calm` jokes rarely, `normal` (the recommended one) lightly and often, `playful` often and with bigger flourishes; nothing else changes. A plugin output style goes into the system prompt of every request, which SessionStart context does not, so the dose is a style file, not a config key. Frontmatter: `keep-coding-instructions: true` (without it Claude Code drops its software-engineering instructions) and no `force-for-plugin` (it would override the user's own `outputStyle`). The text is generic: no user name, company or plugin name.
- Selecting value, proved with the real `claude` CLI (`--plugin-dir <repo>` plus `--settings '{"outputStyle":"..."}'`): a plugin style is addressed as `<plugin>:<name>` with the frontmatter `name`, case-sensitive and lowercase. The values are `juno:juno-calm`, `juno:juno-normal`, `juno:juno-playful`; the bare name `juno` selects nothing.
- `juno init` offers it in step 9: `Juno style: 1) calm  2) normal  3) playful  4) none (Claude's default) [4]: `, default 4 (no style), once (marker `output-style-asked`), interactive only, never with `--dry-run`. 4, an empty answer, EOF or an invalid answer leave the settings alone. 1 to 3 set `outputStyle` in the user settings file with a backup in `install-backups/` and an atomic write, record it in the manifest and print that Claude Code must be restarted. An `outputStyle` that is already set to another value is printed and left alone; one that is already a current `juno:*` value is reported; nothing is asked then. Uninstall removes whichever `juno:*` value was recorded, unless the user changed it since.
- Retired values: `juno:juno` maps to `juno:juno-calm`; `juno:juno-witty`, `juno:juno-warm` and `juno:juno-mentor` map to `juno:juno-normal`. `juno migrate --style [--dry-run]` rewrites such an `outputStyle` in the user settings (`--claude-settings` as for init; backup in `install-backups/`, atomic write, read back; the manifest record follows when it held the old value) and says `output style: nothing to migrate` otherwise; it needs no vault. `init` points to it instead of asking, and `doctor` WARNs `output style` for a retired value, naming the command.
- Styles apply to the main conversation and forks, not to other subagents; this is by design, and subagents keep their own prompts.

### 13.11 `juno import-memory [--dry-run]` (native memory import)
Claude Code's own auto memory lives in `<claude dir>/projects/<project-slug>/memory/` (`JUNO_CLAUDE_PROJECTS` overrides the projects dir, as for cards). `init` turns it off (`autoMemoryEnabled: false`), so the files stay on disk but are never read again. This command (also offered once by `init`, 13.1 step 10) brings them into Juno. Original files are never changed or deleted.
- **Parsing (`core/nativememory.py`, pure).** A memory file is a `*.md` file with frontmatter `name`, `description`, `type` (`user`, `feedback`, `project` or `reference`) and a body. `MEMORY.md` is an index and is skipped, as are other extensions, files over 64 KB, symlinks, files without frontmatter and unknown types. Many real directories are empty. The line stored is the `description`, else the first body line, clipped to 280 characters (the `·` separator becomes `-`). The project slug is the cwd with `/` and `.` replaced by `-`; it maps to a repo when it equals, or starts with `<repo slug>-` (worktrees), the slug of a git repo under `[repos] roots` (longest match); otherwise the note has no repo.
- **Mapping.** `project` and `reference` are written directly: with a repo to the Gotchas section of `projects/<repo>.md`; without one to `knowledge/<topic>.md`, the topic being the slugified note name, as `- <line> · src: import:memory/<project-slug>/<file> · <date>`. `feedback` is queued as a rule candidate (scope `repo:<repo>` or `global`). `user` notes describe the person, so they are queued as *self candidates* in `cards/self-candidates.json` (queue `SELF`, id `s`+6 hex, same JSON format, flock and 180-day decided retention as `candidates.json`): `{id, target: "self", file, text, source, batch: "memory-import", kind: "user", t, status}`. Nothing is written to `self/` or `rules/` before acceptance. A `user` note that trips the self guard (stance or sensitive category, `core/selfguard.py`) is skipped at import and checked again at accept. **Self file rule:** every user note goes to `self/communication.md`. Of the four self files (`values`, `decisions`, `communication`, `tensions`), `communication` is the one about how to work with the person, which is what role, background and preference notes are; `values` and `tensions` would turn a note into a stance or a conflict, and `decisions` needs a choice with a reason (`nativememory.self_file`). An accepted line is written through the self machinery (`selfmodel.record`, verb `self`, evidence `import:memory/<slug>/<file>.md`, no run cap) as `- <text> · <date> · src: import:memory/<slug>/<file>.md`; it has no quote, and the context block drops the `src:` part like it does for `src: user`. Nothing becomes a rule or a self line without the user's choice.
- **Own budget.** Import candidates (`batch: memory-import`) are not counted against the 12-pending cap of ordinary rule candidates (11.5) and are not part of the 4-shown ordinary nudge (`candidates.ordinary_pending`), so 13 imported notes never block a session-end rule candidate. They do not expire while pending (`candidates.prune` skips the batch; a decided one follows the normal 180 days), because the one question may come sessions later. The batch has a hard limit of `nativememory.BATCH_MAX` = 200 pending candidates (rule and self together); notes over it are not queued, the report says `N over the limit of 200`, and a re-run after the pending ones are settled continues idempotently.
- **Safety.** Every line goes through the privacy scan; a refused note is skipped and listed with its path and the reason, never the content. Near duplicates update the existing line like any write; a re-run adds nothing twice (queue ids are a hash of source and text, and an id that was ever queued, decided or not, is not queued again). The import runs as the job identity `job-memory-import-<run>` and refuses inside a Claude session or agent context (the user runs it in a terminal, or answers the init question). Its vault writes (verb `import`, evidence `import:memory/<slug>/<file>.md`) carry one run id, are not counted against `[caps] run_max` (so the cap never truncates the import), and are reverted together with `juno undo --run <id>`, which the command prints. Queued candidates are not part of the undo; reject them with `juno import-memory reject`; an accepted self line is a normal `self` write and is reverted with `juno undo <write id>`. `gate.import_evidence` is the only place that accepts an `import:` evidence string and only the import calls it; `gate.anchor` still demands typed words or `repo:`/`ticket:`, so no verb argument can claim it.
- **Approval in one question.** The rule candidates of the import carry `batch: memory-import` and `source: import:memory/...`. SessionStart does not list them with the one-option-per-candidate question of 11.5; it adds one block that asks once, before the first task, with a single AskUserQuestion (multiSelect false) for the rule and the self candidates together and the options `Accept all memory-import (Recommended)`, `Review memory-import` and `None`. The block lists each candidate as `- <id> · rule · <scope> · <text>` or `- <id> · self · <file> · <text>` and counts both kinds. Accept all runs `juno import-memory accept`, None runs `juno import-memory reject` (no ids: the whole pending batch, both queues, one command; `juno rule accept|reject --batch memory-import` does the same), Review asks one more multiSelect with one option per candidate of either kind (label starts with the id) and runs `juno import-memory accept <ids>` / `reject <ids>` (ids of either kind; `rule accept` takes rule ids only). `juno rule pending` lists both kinds. `accept`/`reject` are not refused inside a Claude session (they must run there); the import itself is. The accept batch is allowed only when the label `memory-import` is in an AskUserQuestion answer the user chose (or typed) in the segment, the same quote-source rule as ids. An accepted line reads `- <date> · <rule> · src: import:memory/<slug>/<file>.md` (`vaultfmt.RULE_LINE_RE` accepts it).
- `--dry-run` reports what would be written and queued and writes nothing.

## 14. Phase 4 write gate

From Phase 4, every vault write goes through a `juno <verb>`. Verbs pass the cheap checks of 8.1 first, so a subagent, a headless run, a missing ledger and `[nosave]` all refuse.

### 14.1 Verbs and fixed routes

| Verb | Writes | Evidence | Notes |
|---|---|---|---|
| `card --learned L [--label --decision --links --tickets --repos] <2-5 lines>` | `handoffs/<routed>.md` | segment | file and heading from `routing.card_targets`; replaces this segment's own card or stub; ledger `card` event. `[[KEY]]` links matching `[jira] prefixes` become tickets of the card (and a ledger `keys` event). `--tickets K1,K2` overrides the card's tickets (heading key by the usual rank, file `handoffs/<KEY>.md`): each key must match the prefixes and have been seen in the segment or be a `[[KEY]]` in `--links`. `--repos r1,r2` overrides the repos: each must be touched in the segment or have `projects/<repo>.md`. Each `--links` entry is `[[target]] (reason)`: an entry with no parenthesised reason is refused, naming the entry and showing `--links "[[repo]] (why this card relies on it)"` (the session-end card run cannot ask, so it drops such an entry and counts it in `links_unlinked`; a card left with none writes `Links: -`). A refusal is one line, exit 2 |
| `knowledge add --topic T [--link TARGET:REASON] "fact"` | `knowledge/<T>.md` | ledger or user | line `- fact · src: <user pid8, repo r, ticket K or web URL> · date`; `--source web --url U [--url U2]` replaces the evidence; the line records `src: web U1, U2`; `--url` repeats at most once and two URLs need different registrable domains (approximated as the last two host labels, three after a two-letter TLD with a generic second level such as `co.uk`, no public-suffix list). `--link` (here and on `note`, repeatable) writes or extends the file's final `Links:` line as `[[target]] (reason)`; the target must resolve through the link index, the reason is 2+ words and 8-200 characters, privacy scanned, without `]]`, `[[`, `\|`, parentheses, `·` or a newline; a repeated target updates its reason; for `knowledge add` the `Links:` line stays the last line |
| `note [--slug --repos --tickets --link TARGET:REASON] "text"` | `notes/YYYY-MM-DD-<slug>.md` | segment | written once; a same-day near-duplicate body is not added, but with `--link` the links are added to that existing note (one write, same checks) |
| `decision --topic T "text" [--supersedes N]` | the owner: `projects/<T>.md` `## Decisions` when a visible profile has that raw name, else `tasks/<KEY>.md` `## Decisions` for a ticket key of a configured `[jira]` prefix (created as a stub with jira, created, updated and url, no title, in the same write), else `decisions/<T>.md` `## Current` | user words | line `- text (user pid8, date)`; `--supersedes` moves line N to `archive/decisions/<T>.md` as one linked write (one undo, one cap unit); refused while an unmigrated legacy file exists (`juno migrate --decisions`) |
| `task log KEY --section S --key K "line"` or `--batch-file F` | `tasks/<KEY>.md` `## <S>`; `digests:` in the frontmatter | user words or `ticket:<KEY>` of that ticket | line `- [K] text · YYYY-MM-DD`, the text 8-1000 characters (other verbs keep 8-300) [#33]; same section and key replaces the line in place; key `^[a-z0-9][a-z0-9._-]{0,39}$`, section `^[A-Z][A-Za-z0-9 -]{1,29}$` (not Notes, Decisions, Links, Current, History); a flagged id is refused with the reason, not masked; `--batch-file` JSON lines `{section,key,line}`, at most 40 rows and 16 KB, read like `backlog` body files (deleted only after the call succeeded, also when nothing changed; a refused call leaves it), a repeated pair refused, one write and one undo; one refused row (length, privacy, shape or a repeated pair) refuses the whole batch and writes nothing |
| `thread add` | `threads.md` | segment | existing verb, now logged |
| `project purpose, run, gotchas or links --repo R "line"` | `projects/<R>.md` section | ledger or user | skeleton created when missing; Purpose holds 3 lines; a `links` line is `[[target]] reason` and the target must be a vault file |
| `project runtime --repo R --block-file F` | `projects/<R>.md` frontmatter `runtime:` block | ledger or user | replaces or inserts the block (30 `  key: value` lines at most, no secret); F lives under /tmp or the state dir and is consumed only when the write succeeded; maestro's `runtime_registry` only reads profiles, its edit comes from retro's Claude-tool step |
| `procedure --slug S --title T "step" ...` | `procedures/<S>.md` | ledger or user | 2-12 numbered steps; the same slug rewrites the file |
| `pitfall [--triggers a,b] "text"` | `pitfalls.md` | ledger or user | the 4-part line of 10.3 |
| `idea --slug S ["text"] [--status --note --outcome]` | `ideas/<S>.md` | segment | creates or updates; status default `planned` |
| `capture "text"` | `inbox.md` | segment | `- text · seg8 · date` |
| `self --file F "line"` | `self/<F>.md` (values, decisions, communication, tensions) | user words | line carries `src: user pid8` and the quote |
| `link <notes\|knowledge/file>.md --link TARGET:REASON [--link ...] --evidence E` | that file | ledger or user | adds to (or starts) the final `Links:` line without touching any other line, as one write (one `undo`); only existing `notes/` and `knowledge/` files; same `--link` rules as `note` |
| `fix <file>.md:<line> --was FRAG "new line"` | that file | ledger or user | the line must contain `--was` (8+ characters); not for `rules/`, `protocol/`, `archive/`, `_generated/`, `core.md` |
| `fix --merge <file>.md:<a>,<b>,.. --was FRAG (one per line) "new line"` | that file and `archive/decayed/forgotten-YYYY-MM.md` | ledger or user | replaces lines of one section with one line as a single write (verb `merge`, plus a linked `merge-archive` record; one cap unit, one `undo`); `consolidate <file>.md [--section S]` is the read-only lister (pitfalldup similarity; backtick paths gone from the default branch: not tracked there AND deleted in its git history, printed with repo, ref and reason; config keys, URLs, refs and never-tracked files are not listed) |
| `forget <file>.md:<line> --was FRAG` | that file and `archive/decayed/forgotten-YYYY-MM.md` | ledger or user | moves the line; two log records, linked. Last body line of a `notes/` file: the file is deleted in the same write (the archive line keeps the note's frontmatter; `undo` restores the file byte for byte) |
| `privacy ack <file>.md:<line> --was DIGITS` | `.privacy-ack.jsonl` | ledger or user | acknowledges one `id` finding (never secret, phone or email): records the file and a hash of the span, so doctor and the session-end card skip it; changed digits need a new ack; `migrate --decisions` re-homes the acks of a moved decision file (13.8); `privacy list` shows them and doctor prints the count |
| `undo <write id>` | the written file | - | see 14.3 |

Evidence kinds: `--evidence` is a verbatim part (at least `quote_min_chars`) of a prompt the user typed or an AskUserQuestion answer the user chose in the session, or `repo:<name>` / `ticket:<KEY>` that the ledger recorded. `decision` and `self` accept only the first kind. Verbs marked "segment" need no flag: the line carries the segment id. `--source web` takes a URL instead, which the ledger cannot anchor.

### 14.2 Every write

1. Scan: secrets (`redact.find_secrets`, after Unicode format characters (Cf) are removed; it covers `NAME=value` with `private_key` among the names and subscript forms such as `os.environ["X_TOKEN"] = ...`, `setx NAME value` and fish `set -x NAME value` where NAME ends in a secret word, and HTTP `Basic <base64>` when it decodes to `user:pass`), then email, phone and 6+ digit ids (`privacy.scan`, 8.3). One-line fields also refuse the `·` separator and keep 8-300 characters (task digest lines, `task log` and the `ticket` kind, 8-1000; 14.1).
2. Anchor the evidence (14.1).
3. Dedupe on the folded first part of the line (before ` · ` and a trailing parenthesis): an exact match writes nothing and prints "already recorded"; a match with similarity at least 0.88 replaces that line (update instead of add).
4. Caps: 100 live writes per session (`[caps] run_max`); notes 2,000, ideas 1,500, cards 1,500 and procedures 2,000 characters. There is no per-file line cap (decision 64).
5. Back up the file (`store.backup`), write atomically under `vault.lock`, append a record `{id, sid, seg, t, verb, file, created, evidence, after, hunks}` to `writes.jsonl` and a ledger `vault_write` event. The write id is `w-` plus 8 hex digits and is printed with the result.

Paths are resolved from the vault root, and a symlink on the way is refused. One check does this everywhere (`store.vault_file`; a compare on NFC and case folded components, with the inode of the vault root as fallback): the gate, the card run, stubs, tasks, backlog, compact and `dashboard --out`. `task set` is a write verb of this gate (verb `task`, evidence `ticket <KEY>`).

### 14.3 Undo

`undo <id>` reverts the line hunks of the record, and of the records linked to it (`forget`, `fix --merge`, `decision --supersedes`, card settle). A linked record is not undone on its own: `undo <id>` of one is refused with `write <id> is part of <parent>; undo <parent>` before anything is written. `undo --run <rid>` reverts every live write of one job run (12.6.1), newest first, and reports the ones it had to refuse. It refuses with `Juno: refused: write <id> cannot be undone cleanly (...)` and a pointer to the last snapshot (`git --git-dir <vault>/.git log -1`) when:
- a later write on the same file touched the same lines (a later write and its own undo cancel out);
- the write created the file and a later write touched it;
- the written lines are no longer in the file.

A write undone once cannot be undone again. A file the write created is deleted when the revert leaves it empty.

### 14.4 PreToolUse refusal

In an interactive session, subagents included, PreToolUse denies Edit, Write, MultiEdit and NotebookEdit when the real path of `file_path` or `notebook_path` is under the vault. It resolves relative paths against the hook's `cwd`, follows symlinks, compares on component boundaries and falls back to the inode of the vault root, so a symlink into the vault and a symlinked vault location are both caught. The reason is one line naming the verb for the file kind (`vaultroute.HINTS`). The same deny covers the state dir entries `ledgers`, `cards`, `agent-calls` and the write log (`writes.jsonl`, `writes.jsonl.1`); the settings deny entries of 8.4 stay as they are. Other paths pass untouched and go on to the topic triggers. Bash writes are not detected (8.3).

### 14.5 Decisions made here

- **Evidence is a user quote or a ledger fact; `note`, `capture`, `idea` and `card` need none.** Reason: the plan asks for evidence anchored in the ledger without saying what counts; the segment id on the line already anchors these four, and a flag would cost every write one more argument.
- **`fix` and `forget` take `file:line` plus `--was`.** Reason: a bare line number goes stale after any other write; the fragment makes a stale ref fail instead of editing the wrong line.
- **Near-duplicate threshold 0.88 (difflib ratio on folded text).** Reason: the simplest standard-library measure; it catches a changed digit or plural and not a different fact.
- **The run cap counts the live writes of the session, 30.** Reason: one session rarely needs more, and an undone write should not eat the budget.
- **`task` is not part of this phase.** Reason: ticket files belong to the Phase 3 Jira adapter, which is a separate branch.
- **Undo works on line hunks, not file snapshots.** Reason: restoring the whole file would erase later unrelated writes; a snapshot is the fallback the refusal points to.
- **The settings merge (8.4) is unchanged.** Reason: the hook deny wins over the allow entry, and the install manifest stays stable.
- `core.md`, `protocol/` and `skills/juno/SKILL.md` tell Claude to use the verbs. Maestro's skills change through the change list handed to the user [plan Phase 4.2].

## 15. Engineering standards and tests

### 15.1 Code
- Runtime is the stdlib only, Python ≥ 3.14. Deferred annotations, no `from __future__ import annotations`. Heavy imports go inside functions, because PEP 810 is 3.15-only. [plan]
- Functional core, imperative shell. `juno/core/` is pure: inputs in, values out, no I/O, no clock, no env. `juno/adapters/` holds file, git, clock, env and process access. [plan]
- A test warns above 600 lines per module [plan]. Comments appear only for constraints the code cannot show [plan]. Every file ends with a newline.
- Dev-only tooling, configured in `pyproject.toml`: `ruff check`, `ruff format`, `mypy --strict` (gates). ty is not a gate. [plan]
- Placeholder grep: no user name, company name or `/opt/homebrew` outside `docs/` and config examples [plan Phase 2 done-when]. A test enforces this. The `launch_path` default in `config.py` is the declared exception.
- **Decided here: the `launch_path` exception.** Reason: it is a PATH list default, not a user path, and it must work on a fresh Mac.

### 15.2 Runner (`tests/run.py`)
The fast suite is described here; end-to-end tests are in 15.3. pytest, xdist and testmon are not adopted [phase0 0.3]. The stdlib `unittest` runner stays.

| Mode | Selection | Notes |
|---|---|---|
| `run.py` | full suite | parallel phase (module-level LPT jobs, default 8 workers, `-j N`), then the serial phase |
| `run.py changed` | affected tests vs `git HEAD` (working tree + index + untracked) | ast import graph plus explicit map |
| `run.py -k PAT` | by name | |
| `run.py --importtime` | none | prints `-X importtime` per hook entry |

- **Import graph.** `ast` parses `juno/` and `tests/` and resolves `import` and `from … import` (relative included) to modules. A test module is selected when it imports a changed module, transitively [plan].
- **Explicit map.** In `pyproject.toml` `[tool.juno-tests]`:
  - `inputs`: glob to test modules, for templates, `vault-template/**`, prompts, schemas, TOML, `hooks/hooks.json`, `bin/*`, `ui/**`, docs that tests scan;
  - `targets`: test module to source modules, for `@subproc` tests and `importlib` dispatch.
  - Unmapped files, `tests/run.py`, `tests/support/**` and `pyproject.toml` itself select the full suite [plan].
  - [phase0 0.3] shows the map is required: the testmon miss was a generated-doc scan.
- **Decided here: the map lives in `pyproject.toml` `[tool.juno-tests]`.** Reason: the plan says `pyproject.toml` holds all tool config.
- **Consistency test.** It fails when a test module is reachable from no source module and is not declared in the map [plan].
- **Serial phase.** Tests marked `@timing` (latency, hook budgets, SessionEnd deadline) run after the parallel phase, one at a time [phase0 0.3: latency tests flake in parallel].
- **`@subproc`.** Real process-boundary tests (launcher, shim, detached child, `-I -S` import bans). `JUNO_TEST_SUBPROC=1` forces every hook and CLI call into a subprocess.
- **Isolation.** Tests use temp dirs through `JUNO_VAULT`, `JUNO_STATE`, `JUNO_CONFIG`, `JUNO_CLAUDE_PROJECTS` and `JUNO_REPO_ROOTS`. Tests never touch the real vault, state, config, `~/.claude` or the repo roots. Tests never hardcode the repo path. v1's copy-path failure [phase0 0.3] is a regression test.
- **Safety net.** The full suite, `ruff check`, `ruff format --check` and `mypy --strict` run before every commit through `.githooks/pre-commit` (`git config core.hooksPath .githooks`, set by `run.py --install-hooks`). A missing dev tool fails the commit with the install line. Selection is never the only gate [plan, phase0]. `@e2e` tests are excluded from the pre-commit suite (15.3).
- **Targets**, measured and printed on every run: affected tests under 5 s, full suite under 60 s on a laptop [plan]. The v1 full suite takes 34.7 s [phase0 0.3]. Reaching under 5 s needs the core split, so that a hook change does not select everything [phase0 0.3].
- **Ported tests.** Every carried module's v1 tests are ported, minus the alarm, mentee, grant and guard tests [plan Phase 2].

### 15.3 End-to-end tests (`tests/e2e/`)

User decision (2026-11-07): every feature is proven end to end. A feature counts as done only when its e2e case has passed.

**Harness.** Each case runs one or more real Claude Code sessions:

```
claude -p "<prompt>" --plugin-dir <repo> --output-format stream-json --verbose [--model haiku] </dev/null
```

- The prompt comes right after `-p`, and stdin is `</dev/null`. [phase0 0.1] found that `claude -p` waits for stdin otherwise, and a Saga pitfall records an empty run when a variadic flag came before the prompt.
- The model is a cheap one (`haiku`) wherever the model's quality does not matter to the assertion. The card-run cases use the configured `[handoff] model`, because card validation depends on the quality of the output. **Decided here.** Reason: with a weaker model, validation failures would be noise rather than bugs.
- **Judge (character cases, #35).** Behaviour that no regex can see (a joke in words, who is teased, calming first, a greeting with one injected unfinished item, an objection with reason and alternative, following the user's decision) is judged by one `sonnet` call per sampled reply. The fixed rubric and the JSON schema of its yes/no fields are in `tests/e2e/judge.py`; the case turns the fields into a pass or fail per criterion, and a judge-only case proves that a planted joke about a production error fails criterion 1. The sampled replies stay on `haiku`. These judged character cases have no timing assertions, so they may run concurrently (one runner process per case, each sample in its own sandbox), unlike the serial e2e run.
- **Env knobs.** `JUNO_E2E_MODEL` sets the reply model for calls that name none (default `haiku`; the judge and the card-run cases are unaffected). `JUNO_E2E_BUDGET` (USD) sets the `--max-budget-usd` cap per call; default 0.5 for `haiku` and 2.0 for any other model, because one `opus` call costs about $0.4 before the reply. A run that stops on the cap (result subtype `error_max_budget_usd`, or an error at cost >= cap) sets `Run.budget_stop` and puts the cap message into `Run.stderr`, which the cases print on a returncode failure. [#35]
- Isolation: a temp vault, state and config through `JUNO_VAULT`, `JUNO_STATE` and `JUNO_CONFIG`, a temp repo root through `JUNO_REPO_ROOTS`. A case never touches the real vault, state, config or `~/.claude` settings. The `--plugin-dir` run needs no settings merge.
- **Interactive override.** `-p` sets `CLAUDE_CODE_SESSION_ATTENDED=0`, and Juno does nothing in non-interactive sessions (7, 8.1). **Decided here: `JUNO_TEST_INTERACTIVE=1` makes the session count as interactive, but only when `JUNO_CONFIG`, `JUNO_VAULT` and `JUNO_STATE` are all set to paths other than their defaults.** Reason: without it no injection or write path could run under `-p`. The extra condition means a stray env var cannot turn on writes in a real headless run. Each case also asserts that without the override the session injects nothing and the CLI refuses, which proves the headless rule.
- Assertions are made only on observable output: vault files (content, line format, `git log` after a snapshot), state files (ledger events, queues, `runs.jsonl`, `status/<sid>.json`, `shadow.jsonl`, `triggers.json`) and the stream-json transcript (`hook_additional_context` attachments, hook success or failure, tool results, the CLI output text).
- Each case also asserts that every hook output is ≤ 9,500 chars and that no hook exit code is non-zero.

**Phase 0 limits, stated per case:**
- *AskUserQuestion is not available in `-p`* [phase0 0.1]. Features that depend on it are tested through their engine code plus a synthetic answer-log input: ledger `ask` and `answer` events, or `self/answers/` lines in Phase 6. This applies to quotes taken from chosen answers, the rule, thread and backlog approval nudges, and the self-model predictions. A case drives the CLI with the same ledger the hooks would have written and asserts on the result. Each such case is marked `gap: askuserquestion` in `docs/e2e.md`, and its live check moves to the Phase 5 live session.
- *SessionEnd's detached run* is asserted by polling after `claude` exits. The case polls `runs.jsonl`, the handoff file and the queues every 0.5 s, up to `[handoff] timeout_seconds` + 10 s. It passes on the expected `status` and fails on timeout, recording the job file.
- PostToolUseFailure firing is unverified in Phase 0. Its case asserts that the ledger `fail` event appears after a failing Bash call.

**Coverage.** There is one case per carried feature (the Carry list in 9 and 10-12, `init`/`uninstall`/`export`/`import`/`migrate`/`doctor` in 13) and one per Phase 3-6 item (16).
- Examples:
  - SessionStart injection of core and global rules;
  - a topic rule injected on a Bash trigger;
  - first-touch repo pack;
  - `[nosave]` blocks every write;
  - session-end card written to the routed file, with extraction lines;
  - late run after a killed session;
  - stub on a failed run;
  - `rule add` with a typed quote;
  - CLI refused without the override;
  - `search` typo tolerance;
  - `snapshot` commits the temp vault;
  - `init` then `uninstall` leaves the settings byte-identical, run with `HOME` set to a temp dir.
- Cases that need several sessions use `--resume` or a sequence of runs sharing the temp state.

**Bookkeeping.** `docs/e2e.md` holds one table row per feature: feature, case id (`tests/e2e/test_<area>.py::<Case>`), spec section, the date of the last run, the result (PASS/FAIL/SKIP), the commit, and a gap note. `tests/run.py --e2e` runs the cases and rewrites the last-run columns. A consistency test fails when a feature listed in 9-13 or 16 has no row.

**Running.** Cases are marked `@e2e`. They are excluded from the default run, from `changed` and from the pre-commit hook, because they spend model budget and take minutes. They run:
- at each phase's Done-when, where the phase is done only when its rows pass;
- before the cutover in Phase 5;
- on demand with `run.py --e2e [-k PAT]`.

Each run prints its total cost from the stream-json `result` events. **Decided here: `--e2e` runs cases serially.** Reason: the SessionEnd polling and the daily card budget are shared state.

## 16. Phases 3-7 (summary; detail in the author's plan, not shipped)

Every Done-when below also requires the phase's `@e2e` rows in `docs/e2e.md` to pass (15.3).

| Phase | Content | Done when |
|---|---|---|
| 3 Jira | `[jira] base_url, prefixes, projects`; no token; ticket title and status from the session's Jira tool through `juno task set`; without `[jira]`, routing is by repo, name or topic | [plan Phase 3] |
| 4 Pre-migration | write gate (14, 9.1); `saga` shim mapping `rule add` and the read verbs (maestro never calls `saga snapshot` [phase0 0.4]; `snapshot` kept for the user's CLAUDE.md); `~/saga` symlink; maestro and mantis change list including the [phase0 0.4] additions (`runtime_registry.py` frontmatter writes, backlog-path scripts, `agents/*.md` vault paths, `~/.claude/skills` symlinks, the stale saga-ui record); rules at cutover: the user decided the policy on 2026-11-07 (4.3), so the AskUserQuestion only confirms the per-rule list the dry run produces; `migrate --dry-run`, `rollback`; acceptance tests | dry-run diff reviewed, rollback tested on a copy, acceptance tests pass |
| 5 Migration | freeze and archive; unload the v1 alarms, mentees and dashboard launchd jobs (their data archived; user decision 2026-11-07); move the vault to `~/juno-vault` and symlink `~/saga` to it; leave `~/saga-engine` untouched; `migrate --from-saga`; v1 `uninstall.py`, `juno init`, plugin, shim, `/juno`; CLAUDE.md lines shown to the user first; fact-key backfill; 14-day rollback window. Check the shadow merge if migration slips past 2026-12-07 [phase0 0.5] | live v2 injection, a card at session end, maestro retro through v2, mantis reads, clean doctor |
| 6 Improvements | one at a time under the experiment protocol (≤ 2 arms, salted hash, pre-registration, interleaving for retrieval): fact keys, agentic search, time-aware search, decisions by topic and consolidation, usage decay, pack content, functional links, procedures, web research (researcher and verifier agents), self model (UserPromptSubmit or topic trigger injection only [phase0 0.1]), next questions; self-improvement levels 1-4; weekly `_generated/reports/` | per item, by its pre-registration |
| 7 Publish | no push by Claude (user decision 2026-11-07): content audit of the repo and its git history, an MIT `LICENSE` and a `README.md`; the repo is left push-ready and the user pushes it with a personal account, so the push-gate conflict in [plan Phase 7] does not arise | repo audited, LICENSE and README present, the user told it is push-ready |

## 17. Decisions made in this spec

Each line points to the section that states the decision and its reason.

1. The repo root is the plugin root, with `hooks/`, `skills/` and `agents/` at the root (3).
2. Marker syntax for invariant `core.md` sections (4.1).
3. The template creates the Phase 6 folders and `inbox.md` empty (4.1).
4. The `decisions/<topic>.md` format (4.2).
5. `decisions` and `self` are protected from retirement (4.2).
6. Migration names decision topics by ticket, else repo, else `misc` (4.3). (history, see 61)
7. The template has no `slack-digest.md`; migration keeps the migrating machine's copy (4.3). (history, see 61)
8. Language preset `none` by default and `tr` on migration; the folding rules (5.1). (migration part: history, see 61)
9. `[repos] overview_roots` (5.1).
10. Shadow on by default (5.1).
11. Linux state in `XDG_STATE_HOME` (5.2).
12. The sh launcher reads the interpreter with `sed`; `[runtime]` is the first table (6.1).
13. One PreToolUse handler group (6.2).
14. PostToolUseFailure on every tool, ledger only (6.2).
15. The PreToolUse backup before vault Edit/Write is removed, and PreToolUse refuses vault Edit/Write instead (6.2, 14.4).
16. No config key for the Linked arm before Phase 6 (6.2).
17. Decisions `## Current` in packs (6.2).
18. The per-event import ban table (6.3).
19. A 1.0 s internal SessionEnd deadline (6.4).
20. `snapshot` needs no quote (8.2).
21. `rule accept`, `thread close` and `backlog accept` keep the id-in-prompt check; `thread keep`, `backlog reject` and `rule reject` do not (8.2).
22. The two `permissions.deny` entries are kept (8.4).
23. `saga decisions` is not carried (9). (history, see 61)
24. `search --archive` indexes the archive, flagged (9).
25. Extraction kinds survive a failed card when anchored (11.2).
26. The `sections` field in the run record (11.4).
27. The shadow merge dedupe key (12.9).
28. No scheduled dashboard job (12.10).
29. The dashboard language follows the preset (12.10).
30. Init does not install the plugin (13.1).
31. The shim's `plugin_root` refresh and `installed_plugins.json` fallback (13.3).
32. Init applies by default, with `--dry-run` to preview (13.4).
33. `export --with-state` (13.7).
34. The `launch_path` default is exempt from the placeholder grep (15.1).
35. The test-selection map lives in `pyproject.toml` `[tool.juno-tests]` (15.2).
36. The card-run e2e cases use the configured handoff model; the other cases use `haiku` (15.3).
37. `JUNO_TEST_INTERACTIVE=1` counts only together with a non-default `JUNO_CONFIG`, `JUNO_VAULT` and `JUNO_STATE` (15.3).
38. `--e2e` runs cases serially (15.3).
39. The `saga` shim keeps the v1 guard sentence for agent and headless refusals, and is installed by default (9.2). (history, see 61)
40. Write gate: evidence kinds, `fix`/`forget` refs with `--was`, 0.88 near-duplicate threshold, the 30-write run cap, line-hunk undo (14.5).
41. Card heading: normalise it deterministically instead of retrying the model; the engine already knows every field but the label, and a retry costs a second paid call and can repeat the slip (11.2).
42. The raw answer log (`asks.jsonl`, indexes only) lives in the state dir, not in the vault (5.2).
43. Arms: salted hash assignment, at most 2 running arms, pre-registered JSON with a sha256 checked at decide; `card_holdout` removed because the holdout never ran and a second arm system would break the cap (12.12).
44. The `juno` skill searches in a loop: 2-4 `juno search` calls with varied wording, Read of the top files, Grep or Glob on the vault for exact identifiers, at most 6 tool calls; no engine change (12.1).
45. Web research (6.9): two plugin agents (`juno:researcher` with WebSearch and WebFetch, `juno:verifier` with WebFetch only, one fresh verifier per claim) and a `research` skill; only CONFIRMED claims are written, by the main session, through `knowledge add --source web` with at most two `--url` values on different registrable domains (a last-two-labels approximation, no new dependency). The engine cannot judge a source as primary, so the skill states that rule; web research never becomes a rule (10.4).
46. Fact keys live in state, keyed by passage hash, and the 5.5 backfill is a foreground verb with a cost cap and a third network exception; retrieval changes are judged by a paired offline comparison, not interleaving (5.2, 12.1, 12.9).
47. The SessionStart context carries the resolved absolute vault path (`Juno vault: <path>.`) and the `juno` skill reads it from there; `core.md` is a static template and cannot name a non-default vault (7, 12.1).
48. Time-aware search (6.3): a per-passage `day` and `sup` in index schema 3, `--since/--until` on `juno search`, and no `superseded_on` field, because it would be a second source of truth that can drift; the skill converts time words to dates, since the gain comes from query-side expansion (12.1).
49. Procedures (6.8): an extraction kind `procedures` writes at most one `procedures/<slug>.md` per run through an engine gate write, because the steps must anchor in the digest (each step has a command, path or version token copied from it, else it is quoted whole) and a wrong procedure injected on a trigger costs more than a missed one; repo-specific run commands stay in `project run`; repo packs list procedure titles in a later change (10.2, 11.4).
50. Purpose is dropped from repo packs behind `[packs] purpose` with no arm: about 200 tail chars, trimmed first, do not justify one of the two arm slots (6.2, 12.2).
51. The Linked block is gated by the `linked` arm, filtered and capped at 5 entries and 500 chars, and the ledger `pack` event logs `nb` in both arms (12.3).
52. Arm metrics read a durable per-segment log (`arms/segments.jsonl`, counts only), written by a sweep at the end of card runs and at `arm status|decide`, because ledgers live 7 days and arms run for weeks (12.12).
53. Fact keys are validated per fact, not per list: one bad fact no longer drops its passage, the reason is stored on empty rows, and an empty row is retried once (`retried: true`). More than 5 entries truncate to the first 5 valid ones, because each kept fact passed the same scan on its own; a non-list still fails (11.4, 12.1).
54. Usage decay (6.5) ships the trace (`usage.jsonl`) and the read-only `juno decay` report only: nothing can decay until 90 days after the trace starts, and a mover without a trace would archive the whole migrated vault; the mover is built when the report first lists a candidate. Grep and Glob log the path they were given, so a directory path never counts for a file; a file's age is its `created` field, else its mtime.
55. Next questions (6.11) are stored in state (`cards/next.jsonl`, by seg8 and card file), not on the card, because the card is shared by both arms; the `next` arm gates them at injection (`[Next · <seg8>]`, at most 400 chars, on-arm only), the ledger `pack` event logs `next_shown` or `next_held` in both arms, and the kind asks and stores nothing unless the arm runs (12.3).
56. The self model (6.10): `self` and `turns` extraction kinds; the engine writes at most 2 quote-verified `self/` lines per run (identity claims, second person and fabricated quotes dropped, 0.88 near-duplicates skipped, no per-file line cap, decision 64), a `Juno model of the user` SessionStart block (`[self] max_lines`, `max_chars`) only on the `self` arm at SessionStart and after compact, never in subagents or headless runs, and the `recommended_pick` and `pushback_rate` metrics over per-segment counters kept in `arms/segments.jsonl` (12.12, 17).
57. The raw answer log folder `vault-template/self/answers` is removed from the template; `asks.jsonl` in state is the measurement log (4.1, 5.2).
58. The pushback guard needs every assistant turn, so the self arm adds a labelled turn list to the card prompt only while it runs and the engine derives the counts from labelled indexes; the pre-registered minima are enforced through an optional `ready` in a metric outcome, leaving `linked` and `next` on the `min_n` rule (12.12).
59. The weekly report (R): `juno report [--week YYYY-Www|last] [--json]` is deterministic (no model call) and writes `_generated/reports/YYYY-Www.md` outside the write gate, reading the existing readers (shadow, asks, runs, writes, hook-errors, topic markers, arms, keys status, usage, inbox). A signal with no data prints `no data`; Level 2 proposals come from a decided arm, a fired stop rule or a significant paired `alt` comparison, Level 3 only names the signal and the non-invariant `core.md` sections, because a diff needs a model. SessionStart (not after compact; headless runs never reach the handler) spawns `juno report --week <last ISO week>` detached when that report is missing and some log exists, at most once per 6 h per week, and adds `Juno: weekly report <week> ready: <path>` once per week (11.2, 12.12).
60. `[keys] weight` defaults to 0 (keys stored, not used in ranking or matching): the first paired comparison after the backfill (2026-11-07, 805 of 811 passages keyed, 53 shadow entries with evidence) found no entry that any weight 0.25-4 hit and weight 0 missed, and 1-4 the other way, as the 6.1 rule pre-registered; re-run `juno eval compare --setting keys` when the evidence set has grown.
61. Saga v1 compatibility is removed (2026-11-08, user decision): no `migrate --from-saga`, no `rollback`, no `saga` shim or `juno init --saga-shim`, no v1-hook guard in hooks, card run and doctor, no `SAGA_DISABLE` and no `---SAGA-EXTRACT---` marker. The cutover finished on 2026-11-07, there will be no rollback, and published installs never had v1; 4.3, 9.2 and the cutover parts of 13.8 are history.
62. The assistant name is fixed as Juno (2026-11-08, user decision): the `[assistant] name` setting is removed, because the `juno` command, the plugin and every `Juno:` output prefix carry the name anyway.

63. Weekly unattended stale fixer (2026-11-08, user decision, chosen over a review queue): with `[stale] auto_fix = true` and `juno schedule install stale-weekly`, `juno stale --fix --job` checks stale knowledge and profile lines against each repo's fetched default branch (never the working tree) with a read-only, directory-confined model, and writes only validated WRONG fixes and GONE archives as actor `job:stale-weekly` with `repo:<name>@<sha>:<path>:<line>` evidence. UNSURE is never written. Writes are capped per file and per run, logged and undoable (`juno undo --run`), snapshotted, and reported at the next SessionStart. Off by default; `juno init` asks once, interactively, and installs the schedule only on a yes. The vault schema is 2 and `core.md` Privacy names the exception (13.8).
64. Per-file line caps are removed (2026-11-08, user decision): the write gate no longer refuses a write because a vault file has too many lines, the pitfalls extractor no longer drops a pitfall as `full`, the self extractor no longer drops a line as `capped` for file size, and doctor no longer WARNs on pitfalls length. Session-start injection is bounded by its own char budgets (`pack_chars`, `pack_knowledge_chars`, `hook_output_chars`), so the line caps no longer controlled context cost and only refused writes mid-session. The 30-write session cap and the `TEXT_MAX` char caps (note, idea, card, procedure) stay.
65. Local default branch for repos without a remote (2026-11-08, user decision): when a repo has no `origin` remote, the stale fixer skips the fetch and pins the committed sha of the first existing local branch of `[stale] branch_order`, never the working tree; the run row shows the ref (`branches`) and fetch `no remote`. A repo that has an `origin` whose fetch fails is still skipped under the 7-day `FETCH_HEAD` rule.
68. Protocol text aligned with the quote rule (2026-11-09, user decision): `protocol/rules.md` said a quote may come from the chosen option's "label or description", but only selected option labels and free text count (8.2), because the assistant writes the descriptions. The template line now says so and tells the user to put the rule's key words in the option label. The stale `core.md` sentence "Stay under the per-file and per-session caps." (per-file caps went with decision 64) is fixed in the same way. Live vaults get both through `juno migrate` step 2 to 3 (13.8), since `juno fix` cannot change `core.md` or `protocol/`.
69. One shared Juno character with a humour dose (#35, 2026-11-16, user decision, supersedes decisions 66 and 67): the four output styles (plain, witty, warm, mentor) were a list of behaviour rules with tones that pulled against the short-answer rule, not a character. There is one character, the same for every user and language (close friend and sharp colleague who jokes and teases the user and itself, never others; answer first; says plainly when it disagrees, then does what the user decides; rarely asks; owns mistakes in one line; loose normal spelling; emoji where they fit; no jokes in serious moments), and one setting, the humour dose `calm`, `normal` or `playful`, delivered as three plugin style files that share a byte-identical character block and differ only in the last dose block (a style is static text in every request, and SessionStart context does not reach every request, so a config key the text reads was rejected). The character applies only to Juno's replies to the user; text drafted to go out under the user's name uses the user's own voice (#34) and skill documents keep their format. Old values map `juno:juno` to `calm` and `juno:juno-witty`, `juno:juno-warm`, `juno:juno-mentor` to `normal`; `juno migrate --style` rewrites them and doctor reports them (13.10).

History (superseded decisions, kept for the record):
- 66. The identity is a plugin output style (2026-11-08, user decision): `output-styles/juno.md` with `keep-coding-instructions: true`, not `core.md`, because a style reaches the system prompt of every request. It is not forced (`force-for-plugin` unset, the user's `outputStyle` wins), `juno init` asks once and defaults to No, and subagents are unaffected by design (13.10). Superseded by decision 69 (#35).
- 67. Four traits and a chosen tone (2026-11-08, user decision): the shared core of the output style carries four behaviour traits (name a contradicted earlier decision, keep memory, checked and inferred apart, lead with the answer and keep replies short, own unfinished items), and `juno init` lets the user choose the tone from plain, witty, warm or mentor (`juno:juno`, `juno:juno-witty`, `juno:juno-warm`, `juno:juno-mentor`); the default is no style. The witty tone never jokes in errors, incidents, bad news or serious moments (13.10). Superseded by decision 69 (#35).

User decisions applied (2026-11-07, not Claude's): the `juno-brain` vault name and move plus the symlink (4.3); the cutover rules policy (4.3); unloading the v1 launchd jobs (16); no push in Phase 7 (16); mandatory e2e proof (15.3); snapshot uses the user's git signing and never turns off gpgSign (12.8).
