import os
import sys

if sys.version_info < (3, 14):
    sys.stderr.write("Juno needs Python 3.14 or newer\n")
    sys.exit(2)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import importlib
from collections.abc import Mapping, Sequence

VERBS: dict[str, tuple[str, str]] = {
    "init": ("init", "create the vault, merge settings, install the shim"),
    "uninstall": ("uninstall", "reverse the settings merge and remove the shim"),
    "doctor": ("doctor", "check the installation"),
    "export": ("export", "write a tarball of the vault and config"),
    "import": ("import_", "restore a vault from a tarball"),
    "migrate": ("migrate", "upgrade the vault schema"),
    "dashboard": ("dashboard", "build the status page (on demand)"),
    "search": ("search", "search the vault (FTS5, typo tolerant)"),
    "recap": ("recap", "recap of recent cards and ticket updates"),
    "stale": ("stale", "flag knowledge and profiles that may be out of date"),
    "eval": ("eval", "measure shadow-logged suggestions (sample, gate, recall, compare, ab)"),
    "arm": ("arm", "pre-registered experiment arms (start, status, stop, decide)"),
    "keys": ("keys", "fact keys for search (backfill, status)"),
    "task": ("task", "write or show tasks/<KEY>.md (needs [jira])"),
    "version": ("version", "print the version"),
    "rule": ("rule", "add, list, remove, move and approve rules"),
    "thread": ("thread", "open, list, close and keep threads"),
    "backlog": ("backlog", "the personal work list"),
    "ideas": ("ideas", "list ideas"),
    "tasks": ("tasks", "list ticket files (needs [jira])"),
    "digest": ("digest", "rebuild _generated/learned.md"),
    "compact": ("compact", "archive old cards"),
    "profiles": ("profiles", "regenerate the tickets blocks of repo profiles (needs [jira])"),
    "snapshot": ("snapshot", "commit the vault (signed)"),
    "card": ("card", "write this segment's handoff card"),
    "knowledge": ("knowledge", "add a durable fact"),
    "note": ("note", "write a situational note"),
    "decision": ("decision", "record a decision"),
    "project": ("project", "add a line to a repo profile section"),
    "procedure": ("procedure", "save a step sequence that worked"),
    "pitfall": ("pitfall", "record a recurring mistake"),
    "idea": ("idea", "save or update an idea"),
    "capture": ("capture", "put a loose item in the inbox"),
    "self": ("self_", "record a line about the user from their words"),
    "fix": ("fix", "correct one vault line"),
    "forget": ("forget", "move a wrong vault line to the archive"),
    "undo": ("undo", "revert a write by its id"),
    "decay": ("decay", "list vault files with no recorded use (read-only)"),
    "report": ("report", "write the weekly report to _generated/reports (no model call)"),
    "schedule": ("schedule", "install, remove or show the weekly stale fixer schedule"),
}


def build_parser(verb: str) -> argparse.ArgumentParser:
    module = importlib.import_module(f"juno.cmd.{VERBS[verb][0]}")
    parser = argparse.ArgumentParser(prog=f"juno {verb}", description=VERBS[verb][1])
    module.add_arguments(parser)
    return parser


def top_help() -> str:
    lines = ["usage: juno <verb> [options]", "", "verbs:"]
    lines += [f"  {name:<10} {text}" for name, (_, text) in VERBS.items()]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write(top_help())
        return 0
    verb = argv[0]
    if verb not in VERBS:
        sys.stderr.write(f"juno: unknown verb {verb!r}\n\n{top_help()}")
        return 2
    args = build_parser(verb).parse_args(argv[1:])
    module = importlib.import_module(f"juno.cmd.{VERBS[verb][0]}")
    code: int = module.run(args, env)
    return code


if __name__ == "__main__":
    sys.exit(main())
