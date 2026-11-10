# juno-ui

Display-only Claude Code mod for Juno. It draws a band above the prompt (the `ui.render` hook on `{ component: 'AbovePrompt' }`, styled `Text` segments in a `Box`) from this session's status file and `$.session.usage()`. It never blocks tools, injects context or writes files, and it runs a process only when the person presses the hook-error item (`juno doctor`) and never otherwise; it does not use `$.ui.status`. Any error shows ` juno │ ?` (dim).

## Status files

The engine writes the same record to `<state>/status/<session id>.json` and to the shared `<state>/status.json` whenever it updates the status (`juno/core/status.py`). The state dir is `$JUNO_STATE` when set. Otherwise the mod tries `~/Library/Application Support/juno` first, then `$XDG_STATE_HOME/juno` (else `~/.local/state/juno`), and uses the first one that holds a fresh file.

The band reads `status/<$.session.id()>.json` and falls back to `status.json` only when its `segment_id` is this session's id (re-read on every poll, so `/clear` and resume switch files). When the mod gets no usable session id it shows `status.json` without the ticket or repo, since that file may be another session's.

Line 1, always:

     juno │ opus 5.5 · ABC-333333 · card ✓  │  ctx ●●○○○ 43%  5h ●○○○○ 12% 3h51  wk ●●●○○ 58% ⇡8  $4.20  cache 91%

With the week running hot (here 68% used at half the window), `wk` turns orange and bold and shows the projected end-of-week usage:

     juno │ opus 5.5 · ABC-333333 · card ✓  │  ctx ●●○○○ 43%  5h ●○○○○ 12% 3h51  wk ●●●○○ 68% ⇡18 →136%  $4.20  cache 91%

- Left: the model (`opus 5.5`, `·1M` for a 1M-context id, family only under 60 columns, raw for unknown ids), then this session's ticket (else repo), then `card ✓` (dim green) or `● dirty` (orange); with `card_auto` true (`[handoff] mode = "end"`, the card is written at session end) no card marker is shown at all. Missing or older than 10 minutes: ` juno │ idle`. The model is re-read on each poll (2 s), so a `/model` change shows within 2 s.
- Right, after a `  │  ` separator and left-aligned: `ctx`, `5h`, `wk`, then any other rate-limit window under its raw name. The meter is 5 dots: `●` filled in the threshold colour, `○` empty and dimmed. Filled dots = round(percent / 20), except under 10% none. The number is always printed after the dots. Colour: blue below 50, yellow 50-70, orange 70-90, red from 90 (which also appends `⚠`). `5h` shows its reset countdown; `wk` shows pace against the 7-day window (`⇡<pace>` only when above +2, red above +15). `5h n/a` / `wk n/a` when a window is missing; `ctx` is hidden when absent.
- Weekly warning: `wk` (label orange and bold) and `→<n>%` appear when the 7-day usage is 70% or more, or runs more than 10 points ahead of the elapsed time. `→<n>%` is the linear end-of-week projection, left out until 5% of the window has passed.
- Last: `$<usd>` is the session cost and `cache <n>%` is the share of this session's main-loop prompt tokens served from the prompt cache, summed from each main-loop `turn.complete` since `session.start`.

Line 2, only when something needs attention, ` ╰ ` then items joined by `  ·  `:

1. `⚠ N hook errors in 24h — juno doctor` (red)
2. `▸ handoff card out of date: <card path>` (orange; not shown when `card_auto` is true)
3. `+ rule added (<scope>) <first 50 chars>` (green, 60 s)
4. `▸ handoff reminder sent` (60 s)
5. `⚠ <limit> at <p>%` for each window at 90% or more

Pressable items (underlined; Tab or click, then Enter): `● dirty` and `▸ handoff card out of date` open the pane `juno-card` with the card file's text (read with `$.fs.read`; a dim line when the file is missing), and `⚠ N hook errors` opens `juno-doctor`, which runs `juno doctor` once per press (never on a poll) and shows its stdout, plus stderr on a non-zero exit. Each pane has a Dismiss button.

Narrow terminals (width is `bodyColumns` of the band): under 110 columns the meters' dots go, under 80 the reset, pace and cache go, under 60 the cost goes too. Line 2 is cut with `…`.

`JUNO_UI_FINE_BARS=1` opts into the eighth-block bar instead of dots. `NO_COLOR` set: no colours. A non-UTF-8 locale, `TERM=dumb` or `JUNO_UI_ASCII=1`: ASCII bars `[###--]` and ASCII symbols. `JUNO_UI_DEBUG=1` logs the raw `rateLimits` and the resolved status paths.

## Trial

    claude --plugin-dir <repo>/ui

Check with `/plugin` ("1 mod active · juno-ui"). Offline check: `claude plugin validate <repo>/ui` and `node ui/test/format.test.mjs`.

## Verify first

- The band draws (check the transcript for `ui.render (AbovePrompt) refused: ...` if not), and Text `color` accepts the raw `#ff8c00`.
- `rateLimits` is populated on the plan in use and the `kind` names match (`five_hour`, `seven_day`).
- `$.session.id()` matches the hook payload's `session_id` (the per-session file name), including after `/clear` and resume.
- Shape of `last_rule` in status.json (the line reads `scope` and `text` if it is an object).

## Install

    claude plugin install juno-ui@juno-local

The marketplace entry is `./ui` in `.claude-plugin/marketplace.json`; it installs apart from the `juno` plugin and needs no settings merge.
