"""The doctor contract (#28): a doctor message that names a Juno command is cleared by it.

Each row builds a vault that trips one doctor check, runs doctor, runs the command the message
names, runs doctor again, and asserts the message is gone. A command that only lists (class
`lists`) must leave the message in place and the message must say it lists. A source scan
fails when a doctor message names a command that no row covers.
"""

import ast
import glob
import json
import os
import re
import shutil
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from unittest import mock

from juno.core.privacy import span_hash
from tests import test_protocolsync as protocolsync
from tests.support import REPO, decision_vault, fakeclaude
from tests.support import legacy_vault as legacy
from tests.support.write import WriteCase

QUOTE = "this is the first typed prompt"
COMMAND_RE = re.compile(r"\bjuno ([a-z]+)")
LISTS_RE = re.compile(r"\b(lists?|shows?|pending)\b")
SCANNED = (
    "juno/cmd/doctor.py",
    "juno/cmd/doctor_vault.py",
    "juno/core/doctor_vault.py",
    "juno/core/doctor_fit.py",
    "juno/core/doctor_decisions.py",
    "juno/core/doctor_links.py",
    "juno/core/doctor_profiles.py",
    "juno/core/cardcopies.py",
    "juno/core/notebody.py",
    "juno/core/privacyack.py",
)
# Not scanned on purpose: `claude plugin ...` in the plugin and plugin cache messages is not
# Juno's command, so a row cannot run it.

Setup = Callable[[WriteCase], None]


@dataclass(frozen=True)
class Row:
    key: str
    needle: str  # the command as it is written in the doctor source literal
    check: str  # doctor check name
    marker: str  # a piece of the detail that picks this message out
    argv: tuple[str, ...]
    setup: Setup
    lists: bool = False  # the command only lists: the message stays and says so


def edit(case: WriteCase, rel: str, change: Callable[[str], str]) -> None:
    case.put(rel, change(case.get(rel)))


def legacy_vault(case: WriteCase) -> None:
    legacy.build(case.sb.vault)


def cut_config(case: WriteCase, prefix: str) -> None:
    with open(case.sb.config, encoding="utf-8") as handle:
        lines = [x for x in handle.read().splitlines() if not x.startswith(prefix)]
    with open(case.sb.config, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def no_interpreter(case: WriteCase) -> None:
    cut_config(case, "python")


def no_state(case: WriteCase) -> None:
    shutil.rmtree(case.sb.state)


def no_vault(case: WriteCase) -> None:
    shutil.rmtree(case.sb.vault)


def bad_plugin_root(case: WriteCase) -> None:
    with open(case.sb.config, encoding="utf-8") as handle:
        text = handle.read()
    with open(case.sb.config, "w", encoding="utf-8") as handle:
        handle.write(re.sub(r'plugin_root = ".*"', 'plugin_root = "/nonexistent/juno"', text))


def no_shim(case: WriteCase) -> None:
    os.unlink(os.path.join(case.sb.bin, "juno"))


def no_manifest(case: WriteCase) -> None:
    """A settings file Juno has not merged into yet: `juno init` merges and writes the manifest."""
    os.unlink(os.path.join(case.sb.state, "install-manifest.json"))
    with open(case.sb.settings, "w", encoding="utf-8") as handle:
        handle.write("{}\n")


def old_protocol(case: WriteCase) -> None:
    case.put(f"protocol/{protocolsync.NAME}", protocolsync.old_text())


def missing_protocol(case: WriteCase) -> None:
    os.unlink(case.vp("protocol/links.md"))


def stubs(case: WriteCase) -> None:
    text = "## 2026-11-01 10:00 · - · work · bbbb0001\nRepos: app\nTickets: -\n"
    case.put("handoffs/app.md", "# H\n\n" + text + "auto: true\nLearned: none\n\n")


def duplicates(case: WriteCase) -> None:
    one = legacy.card("bbbb0002", "app")
    case.put("handoffs/app.md", legacy.KEEP + one + one)


def many_cards(case: WriteCase) -> None:
    body = "".join(legacy.card(f"{i:08x}", "app") for i in range(25))
    case.put("handoffs/app.md", legacy.KEEP + body)


def owned_decisions(case: WriteCase) -> None:
    case.set_config('[jira]\nprefixes = ["ABC"]')
    decision_vault.build(case.sb.vault)


def orphaned_ack(case: WriteCase) -> None:
    """A vault the 1.0.11 move already ran on: the ack still points at the removed file."""
    owned_decisions(case)
    case.juno("migrate", "--decisions")
    line = "- Pull from the mirror at 000000000000 (user pid00001, 2026-11-01)"
    edit(
        case, "tasks/ABC-1.md", lambda t: t.replace("## Decisions\n", f"## Decisions\n{line}\n", 1)
    )
    rec = {"file": "decisions/ABC-1.md", "h": span_hash("000000000000"), "line": 3, "t": "x"}
    case.put(".privacy-ack.jsonl", json.dumps(rec) + "\n")


def ticket_rules(case: WriteCase) -> None:
    case.put(
        "rules/ABC-12.md", "- 2026-01-01 · typed prompt retries stay bounded · src: unquoted\n"
    )


PITFALL = "- {} · triggers: {} · evidence · 2026-11-01\n"


def twin_pitfalls(case: WriteCase) -> None:
    from tests import test_pitfalldup as twins

    case.put(
        "pitfalls.md",
        PITFALL.format(twins.CLAUDE_A, "claude, flags") + PITFALL.format(twins.CLAUDE_B, "claude"),
    )


def synthetic_prompt(case: WriteCase) -> None:
    case.prompt("<local-command-stdout>ok</local-command-stdout>", "pid00002")


def corrupt_index(case: WriteCase) -> None:
    case.juno("search", "anything")
    with open(os.path.join(case.sb.state, "index.sqlite3"), "wb") as handle:
        handle.write(b"this is not a sqlite database" * 50)


def drifted_digest(case: WriteCase) -> None:
    case.put("handoffs/app.md", legacy.KEEP + legacy.card("bbbb0003", "app"))


def pending_thread(case: WriteCase) -> None:
    from juno.adapters import queues

    line = "- watch the nightly build · app · since 2026-01-01"
    case.put("threads.md", f"# Threads\n\n{line}\n")
    case.seed_queue(
        queues.THREADS,
        [
            {
                "id": "t111111",
                "line": line,
                "signals": ["PR app#4 merged"],
                "t": 1.0,
                "status": "pending",
            }
        ],
    )


def id_finding(case: WriteCase) -> None:
    case.put(
        "notes/sample.md", "# Sample\n\n- The mirror uses the 000000000000 pull-through cache\n"
    )
    code, out = case.juno(
        "privacy", "ack", "notes/sample.md:3", "--was", "000000000000", "--evidence", QUOTE
    )
    assert code == 0, out


def unkeyed_passages(case: WriteCase) -> None:
    case.set_config("[keys]\nweight = 0.5")
    for i in range(3):
        case.put(f"notes/item{i}.md", f"# Note {i}\n\n{'Billing runs nightly. ' * 12}variant {i}\n")
    facts = {
        str(i): [f"fact for passage {i} first", f"fact for passage {i} second"]
        for i in range(1, 60)
    }
    fakeclaude.answer(case.env, json.dumps(facts))
    patch = mock.patch.dict(os.environ, case.env)
    patch.start()
    case.addCleanup(patch.stop)


ROWS = (
    # install and config: `juno init` writes them back
    Row(
        "interpreter",
        "juno init",
        "interpreter",
        "no interpreter cached",
        ("init",),
        no_interpreter,
    ),
    Row("state", "juno init", "state", "missing; run juno init", ("init",), no_state),
    Row("vault", "juno init", "vault", "run juno init", ("init",), no_vault),
    Row(
        "plugin root", "juno init", "plugin root", "has no juno/cli.py", ("init",), bad_plugin_root
    ),
    Row("shim", "juno init", "shim", "missing; run juno init", ("init",), no_shim),
    Row("manifest", "juno init", "settings merge", "no install manifest", ("init",), no_manifest),
    # cards
    Row(
        "copies",
        "juno migrate --cards",
        "card copies",
        "juno migrate --cards",
        ("migrate", "--cards"),
        legacy_vault,
    ),
    Row(
        "misfiled",
        "juno migrate --cards",
        "misfiled cards",
        "juno migrate --cards",
        ("migrate", "--cards"),
        legacy_vault,
    ),
    Row(
        "stubs dismiss",
        "juno card dismiss",
        "stubs",
        "juno card dismiss",
        ("card", "dismiss", "bbbb0001", "--evidence", QUOTE),
        stubs,
    ),
    Row(
        "duplicates",
        "juno card merge",
        "duplicate cards",
        "juno card merge",
        ("card", "merge", "app", "bbbb0002", "--evidence", QUOTE),
        duplicates,
    ),
    Row("compact", "juno compact", "handoffs", "juno compact", ("compact",), many_cards),
    Row("digest", "juno digest", "learned", "not built yet", ("digest",), drifted_digest),
    Row(
        "decisions",
        "juno migrate --decisions",
        "decisions",
        "juno migrate --decisions",
        ("migrate", "--decisions"),
        owned_decisions,
    ),
    Row(
        "lineless decisions",
        "juno migrate --decisions",
        "decisions",
        "has no current lines",
        ("migrate", "--decisions"),
        owned_decisions,
    ),
    Row(
        "orphaned acks",
        "juno migrate --decisions",
        "decisions",
        "privacy acknowledgements point at",
        ("migrate", "--decisions"),
        orphaned_ack,
    ),
    # notes, rules, pitfalls
    Row(
        "notes",
        "juno migrate --notes",
        "note bodies",
        "juno migrate --notes",
        ("migrate", "--notes"),
        legacy_vault,
    ),
    Row(
        "rule move",
        "juno rule move",
        "rules",
        "juno rule move",
        ("rule", "move", "--from", "ticket:ABC-12", "--to", "repo:app", "--quote", QUOTE, "1"),
        ticket_rules,
    ),
    Row(
        "forget",
        "juno forget",
        "pitfalls",
        "juno forget",
        ("forget", "pitfalls.md:2", "--was", "Running claude -p", "--evidence", QUOTE),
        twin_pitfalls,
    ),
    # protocol, index, stale, synthetic
    Row(
        "protocol outdated",
        "juno migrate --protocol",
        "protocol",
        "older shipped",
        ("migrate", "--protocol"),
        old_protocol,
    ),
    Row(
        "protocol missing",
        "juno migrate --protocol",
        "protocol",
        "is missing",
        ("migrate", "--protocol"),
        missing_protocol,
    ),
    Row(
        "index",
        "juno search",
        "index",
        "no search index yet",
        ("search", "anything"),
        lambda c: None,
    ),
    Row(
        "index broken", "juno search", "index", "rebuilds it", ("search", "anything"), corrupt_index
    ),
    Row(
        "stale cache",
        "juno stale",
        "stale cache",
        "no stale-memory check",
        ("stale",),
        lambda c: None,
    ),
    Row(
        "synthetic",
        "juno doctor --purge-synthetic",
        "synthetic",
        "purge-synthetic",
        ("doctor", "--purge-synthetic"),
        synthetic_prompt,
    ),
    Row(
        "keys",
        "juno keys backfill",
        "keys",
        "juno keys backfill",
        ("keys", "backfill"),
        unkeyed_passages,
    ),
    # the command only lists: the message stays and says that it lists
    Row(
        "consolidate",
        "juno consolidate",
        "pack fit",
        "juno consolidate",
        ("consolidate", "projects/alpha.md", "--section", "Gotchas"),
        legacy_vault,
        lists=True,
    ),
    Row(
        "thread stale",
        "juno thread list --stale",
        "threads",
        "juno thread list --stale",
        ("thread", "list", "--stale"),
        pending_thread,
        lists=True,
    ),
    Row(
        "thread pending",
        "juno thread pending",
        "threads",
        "juno thread pending",
        ("thread", "pending"),
        pending_thread,
        lists=True,
    ),
    Row(
        "privacy list",
        "juno privacy list",
        "privacy",
        "juno privacy list",
        ("privacy", "list"),
        id_finding,
        lists=True,
    ),
)


def literals(path: str) -> list[str]:
    """String literals of one module that are code, not docstrings; f-strings keep `{}` holes."""
    with open(os.path.join(REPO, path), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
        if isinstance(node, ast.JoinedStr):
            skip.update(id(part) for part in node.values)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            found.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            found.append(
                "".join(
                    p.value if isinstance(p, ast.Constant) else "{}"
                    for p in node.values
                    if isinstance(p, (ast.Constant, ast.FormattedValue))
                )
            )
    return [x for x in found if COMMAND_RE.search(x)]


class Contract(WriteCase):
    def checks(self) -> list[dict[str, str]]:
        _, out = self.juno("doctor", "--json", *self.sb.install_args())
        return list(json.loads(out)["checks"])

    def found(self, row: Row) -> list[str]:
        return [
            f"{c['status']} {c['detail']}"
            for c in self.checks()
            if c["name"] == row.check and row.marker in c["detail"]
        ]

    def run_row(self, row: Row) -> None:
        row.setup(self)
        before = self.found(row)
        self.assertTrue(before, f"{row.key}: doctor does not raise the message")
        args = row.argv + tuple(self.sb.install_args() if row.argv[0] in ("init", "doctor") else ())
        code, out = self.juno(*args)
        self.assertEqual(code, 0, f"{row.key}: {' '.join(row.argv)} exited {code}\n{out}")
        after = self.found(row)
        if row.lists:
            self.assertTrue(out.strip(), f"{row.key}: the command printed nothing")
            self.assertEqual(after, before, f"{row.key}: a lister changed the message")
            self.assertTrue(LISTS_RE.search(before[0]), f"{row.key}: the text must say it lists")
        else:
            self.assertEqual(after, [], f"{row.key}: {' '.join(row.argv)} left {after}")


def make(row: Row) -> Callable[[Contract], None]:
    def test(self: Contract) -> None:
        self.run_row(row)

    return test


for _row in ROWS:
    setattr(Contract, "test_" + re.sub(r"\W+", "_", _row.key), make(_row))


class Coverage(unittest.TestCase):
    def test_every_doctor_message_that_names_a_command_has_a_row(self) -> None:
        uncovered = []
        for path in SCANNED:
            for text in literals(path):
                for found in COMMAND_RE.finditer(text):
                    ok = any(
                        row.needle in text and row.needle.split(" ")[1] == found.group(1)
                        for row in ROWS
                    )
                    if not ok:
                        uncovered.append(f"{path}: {text[:90]!r}")
        self.assertEqual(uncovered, [], "add a row to ROWS in tests/test_doctor_contract.py")

    def test_every_needle_is_still_in_the_source(self) -> None:
        source = [t for path in SCANNED for t in literals(path)]
        for row in ROWS:
            self.assertTrue(any(row.needle in t for t in source), f"{row.key}: {row.needle}")

    def test_every_doctor_module_is_scanned(self) -> None:
        found = {
            os.path.relpath(p, REPO)
            for pattern in ("juno/**/doctor*.py", "juno/**/cardcopies.py", "juno/**/notebody.py")
            for p in glob.glob(os.path.join(REPO, pattern), recursive=True)
        }
        self.assertEqual(sorted(found - set(SCANNED)), [])


if __name__ == "__main__":
    unittest.main()
