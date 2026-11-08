# Juno

Juno is one person's local memory for Claude Code. It is a Claude Code plugin plus a `juno` command line tool. Hooks inject what you decided and learned earlier into each new session, and at the end of a session Juno writes a short handoff card into a vault of plain Markdown files on your own disk.

Everything runs locally with the Python standard library. There are no runtime dependencies, no server and no account.

## Requirements

- Python 3.14 or newer (`requires-python = ">=3.14"` in `pyproject.toml`). `juno init` looks for a suitable interpreter and records it.
- Claude Code, with the `claude` command on your PATH.
- `git`, for the vault's own snapshot history.
- macOS or Linux. The tests use a POSIX shell.

## Install

1. Clone or unpack this repository somewhere permanent. Its root is the plugin root.
2. Create your vault and wire Juno into Claude Code:

   ```sh
   ./bin/juno init
   ```

   `init` asks nothing and can be run again safely. Use `./bin/juno init --dry-run` to preview. It does exactly this:

   - writes `juno.toml` (default `~/.config/juno/juno.toml`) with the interpreter and plugin root;
   - creates the state directory (on macOS `~/Library/Application Support/juno`, elsewhere `~/.local/state/juno`);
   - creates an empty vault from `vault-template/` (default `~/juno-vault`) and runs `git init` in it. An existing vault is never overwritten;
   - merges a small set of entries into `~/.claude/settings.json`: the vault as an additional directory, read and edit permissions for it, a deny on editing `rules/` and `.git/`, and `autoMemoryEnabled: false`. The original file is backed up first and every added entry is recorded in `install-manifest.json`;
   - installs a `juno` command at `~/.local/bin/juno`. Make sure that directory is on your PATH.

3. Install the plugin in Claude Code. `init` prints the exact commands; they look like this:

   ```sh
   claude plugin marketplace add "<path to this repository>"
   claude plugin install juno@juno-local
   ```

4. Check the result with `juno doctor`.

To remove Juno, run `juno uninstall`. It reverts exactly what `init` changed in your Claude settings (it restores the original bytes when nothing else touched the file, otherwise it removes only the recorded entries), and removes the `juno` command. It never deletes your vault or the state directory. Remove the plugin itself with `claude plugin uninstall juno@juno-local`.

## First steps

- Start Claude Code in a project and work as usual. Juno only acts in interactive sessions; sessions run with `-p` or from scripts never read or write the vault.
- Ask `/juno <question>` to search past work, or run `juno search <words>` in a terminal.
- Teach it a durable preference with `juno rule add` (it requires `--quote`, a verbatim part of a prompt you typed). Other useful verbs: `juno recap`, `juno tasks`, `juno ideas`, `juno backlog`, `juno stale`. Run `juno --help` for all of them.
- Look inside the vault. It is plain Markdown, and Obsidian opens it well.

## Privacy

The vault stays on your machine. Juno has no telemetry and sends nothing anywhere; the vault is a separate git repository with no remote, outside this code repository, so no `git add` here can publish your memory. The only network use is what you configure yourself, for example the optional `gh` calls for open pull requests under `[threads] gh_owner`. The session-end card run starts your own `claude` command, so its prompt goes where your Claude Code sessions already go.

Do not copy a vault between people: it holds the owner's decisions, corrections and private notes. See `docs/colleague.md`. `juno export` and `juno import` move your own vault to another machine of yours.

## Configuration

Settings live in `juno.toml`. Every key has a default and nothing is required. The full list is in section 5.1 of `docs/spec.md`. The keys you will most likely set:

| Key | Default | Meaning |
|---|---|---|
| `[paths] vault` | `~/juno-vault` | where the vault lives (the `JUNO_VAULT` environment variable overrides it) |
| `[paths] state` | platform default | logs, index, ledgers |
| `[jira] prefixes` | `[]` | ticket key prefixes such as `["DEF"]`. Empty means Jira is off: no ticket detection |
| `[jira] base_url` | `""` | used only to build links to tickets |
| `[repos] roots` | `["~/projects", "~/code", "~/src"]` | folders whose subfolders are treated as repos |
| `[language] preset` | `none` | `tr` adds Turkish folding and cues |
| `[threads] gh_owner` | `""` | GitHub owner for pull request lookups. Empty means no `gh` call |
| `[handoff] mode` | `end` | when the session-end card is written |

Juno never calls Jira itself and holds no token. Ticket titles come from whatever Jira tool your Claude session already has.

## Tests

```sh
python3 tests/run.py
```

This runs the unit suite in parallel (a few seconds to half a minute). For lint and type checks use a virtualenv with `ruff` and `mypy`:

```sh
ruff check . && ruff format --check . && mypy --strict juno
```

End-to-end cases live in `tests/e2e/` and are described in `docs/e2e.md`. They start real `claude` sessions, so they need Claude Code to be logged in and they cost tokens; they are opt-in: `python3 tests/run.py --e2e -k <name>`. A run updates the result columns of `docs/e2e.md`.

Two optional guard tests refuse certain strings in shipped files. To add your own (a company or personal name, for example), put one per line in a file called `.juno-banned.txt` at the repository root (it is gitignored) or point `JUNO_BANNED_FILE` at one.

## More

- `docs/spec.md`: the design and every config key.
- `docs/colleague.md`: using Juno as one of several colleagues.
- `docs/e2e.md`: end-to-end cases.
- `ui/README.md`: the optional status band plugin (`juno-ui`).

Licensed under the MIT License, see `LICENSE`.
