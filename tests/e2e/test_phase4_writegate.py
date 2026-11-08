import json
import os
import re
import uuid

from tests.e2e.harness import JUNO_BIN
from tests.e2e.test_phase2_cli import Case, command
from tests.support import e2e


def write_log(case: Case) -> list[dict[str, object]]:
    path = os.path.join(case.sb.state, "writes.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle.read().splitlines()]


def notes(case: Case) -> list[str]:
    directory = case.vp("notes")
    return (
        sorted(n for n in os.listdir(directory) if n.endswith(".md"))
        if os.path.isdir(directory)
        else []
    )


@e2e
class VaultWriteRefusedThenVerb(Case):
    def test_write_tool_on_a_vault_file_is_refused_and_the_model_uses_the_verb(self) -> None:
        token = "wg" + uuid.uuid4().hex[:8]
        sentence = f"The nightly export finishes in forty minutes ({token})"
        target = self.vp("notes/e2e-direct.md")
        run = self.go(
            f"Use the Write tool (not Bash) to create the file {target} containing exactly this "
            f"sentence: {sentence}. If the tool is refused, do what the refusal message says, "
            f"but run the command as {JUNO_BIN} (the full path) instead of plain juno, with "
            f"the same sentence as the note text. Then reply with the word done.",
            allow=["Write", "Bash"],
        )
        out = self.output(run)
        self.assertIn("Juno: refused", out, run.raw[-3000:])
        self.assertFalse(os.path.exists(target), "the Write tool must not reach the vault")
        found = [n for n in notes(self) if token in self.get("notes/" + n)]
        self.assertTrue(found, f"no note written through the verb: {run.raw[-3000:]}")
        records = [r for r in write_log(self) if r["file"] == "notes/" + found[0]]
        self.assertEqual([r["verb"] for r in records], ["note"])
        self.assertRegex(str(records[0]["id"]), r"^w-[0-9a-f]{8}$")


@e2e
class NoteThenUndoRestores(Case):
    def test_undo_removes_the_note_the_verb_wrote(self) -> None:
        token = "wg" + uuid.uuid4().hex[:8]
        run = self.go(
            "Step 1: "
            + command(JUNO_BIN, "note", f'"The staging bucket is renamed on Fridays ({token})"')
            + " Step 2: read the write id (w- followed by 8 hex characters) from that output, then "
            + command(JUNO_BIN, "undo", "<that write id>")
            + " Reply with the word done.",
        )
        out = self.output(run)
        self.assertRegex(out, r"Juno: note written to notes/", run.raw[-3000:])
        self.assertRegex(out, r"Juno: write w-[0-9a-f]{8} reverted in notes/", run.raw[-3000:])
        self.assertEqual([n for n in notes(self) if token in self.get("notes/" + n)], [])
        verbs = [r["verb"] for r in write_log(self)]
        self.assertEqual(verbs, ["note", "undo"])
        ids = re.findall(r"w-[0-9a-f]{8}", out)
        self.assertIn(str(write_log(self)[0]["id"]), ids)


@e2e
class SecretLookingNoteRefused(Case):
    def test_a_secret_is_refused_and_nothing_is_written(self) -> None:
        run = self.go(
            "This is a test of Juno's own secret gate, with a made-up fixture string that is not a "
            "real credential. The gate, not you, decides: it refuses a secret and writes nothing, "
            "so running the command is safe and is the whole test; do not judge the text yourself. "
            "Build the fixture by joining these two pieces with no space between them: piece one is "
            "'ghp_' and piece two is 'Ab1Cd2Ef3Gh4Ab1Cd2Ef3Gh4Ab1Cd2Ef3Gh4'. "
            + command(
                JUNO_BIN,
                "note",
                '"Remember the deploy token <the joined fixture> for the export job"',
            )
        )
        out = self.output(run)
        self.assertIn("Juno: refused: note looks like it contains private data (secret)", out)
        self.assertEqual(notes(self), [])
        self.assertEqual(write_log(self), [])
