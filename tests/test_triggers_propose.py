"""#36 follow-up: `juno triggers propose` queues a trigger candidate per untriggered file."""

import hashlib
import os
import re
import unittest
from unittest import mock

from juno.adapters import knowledgetriggers, queues
from juno.core import candidates, triggerprop
from tests.support.write import WriteCase

FILES = {
    "knowledge/alpha.md": "---\ntitle: Alpha pipeline\nsources: [x]\n---\nThe pipeline retries.\n",
    "knowledge/beta.md": "---\ntitle: Beta gateway\nsources: [x]\n---\nThe gateway times out.\n",
    "knowledge/gamma.md": "---\ntitle: Gamma scheduler\nsources: [x]\n---\nScheduler drift.\n",
    "knowledge/done.md": "---\ntitle: Done\nsources: [x]\ntriggers: done-thing\n---\nbody\n",
}


def digest(root: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for base, _, names in os.walk(root):
        for name in names:
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                found[os.path.relpath(path, root)] = hashlib.sha256(handle.read()).hexdigest()
    return found


class Propose(WriteCase):
    def setUp(self) -> None:
        super().setUp()
        for rel, text in FILES.items():
            self.put(rel, text)

    def triggers_items(self) -> list[queues.Item]:
        return [i for i in queues.read(self.cfg, queues.RULES) if candidates.is_triggers(i)]

    def test_criterion_10_one_candidate_per_untriggered_file_and_no_file_changed(self) -> None:
        before = digest(self.sb.vault)
        code, out = self.juno("triggers", "propose")
        self.assertEqual(code, 0, out)
        self.assertIn("3 trigger candidates queued", out)
        items = self.triggers_items()
        self.assertEqual(
            sorted(i["file"] for i in items),
            ["knowledge/alpha.md", "knowledge/beta.md", "knowledge/gamma.md"],
        )
        self.assertTrue(all(i["status"] == "pending" and i["triggers"] for i in items))
        self.assertEqual(digest(self.sb.vault), before)

    def test_criterion_11_accepting_one_changes_only_that_file(self) -> None:
        self.juno("triggers", "propose")
        items = {i["file"]: i for i in self.triggers_items()}
        alpha = items["knowledge/alpha.md"]
        before = digest(self.sb.vault)
        self.prompt(f"yes, accept {alpha['id']}", "pid00002")
        code, out = self.juno("rule", "accept", alpha["id"])
        self.assertEqual(code, 0, out)
        after = digest(self.sb.vault)
        changed = {k for k in after if k.startswith("knowledge/") and before.get(k) != after[k]}
        self.assertEqual(changed, {"knowledge/alpha.md"})
        self.assertRegex(self.get("knowledge/alpha.md"), r"(?m)^triggers: ")
        self.assertNotIn("triggers:", self.get("knowledge/beta.md"))
        status = {i["file"]: i["status"] for i in self.triggers_items()}
        self.assertEqual(status["knowledge/alpha.md"], "accepted")
        self.assertEqual(status["knowledge/beta.md"], "pending")
        self.assertEqual(status["knowledge/gamma.md"], "pending")

    def test_criterion_12_a_second_run_queues_nothing_new(self) -> None:
        self.juno("triggers", "propose")
        first = self.triggers_items()
        code, out = self.juno("triggers", "propose")
        self.assertEqual(code, 0, out)
        self.assertIn("0 trigger candidates queued", out)
        self.assertIn("3 already queued", out)
        self.assertEqual(self.triggers_items(), first)

    def test_a_decided_candidate_is_not_proposed_again(self) -> None:
        self.juno("triggers", "propose")
        beta = next(i for i in self.triggers_items() if i["file"] == "knowledge/beta.md")
        self.prompt(f"reject {beta['id']}", "pid00002")
        self.juno("rule", "reject", beta["id"])
        self.juno("triggers", "propose")
        self.assertEqual(len(self.triggers_items()), 3)

    def test_ordinary_slots_are_not_used_and_the_batch_has_its_own_limit(self) -> None:
        self.juno("triggers", "propose")
        queue = queues.read(self.cfg, queues.RULES)
        self.assertTrue(all(i["batch"] == candidates.PROPOSE_BATCH for i in queue))
        self.assertEqual(candidates.ordinary_pending(queue), [])
        self.assertEqual(candidates.rules_nudge(queue), "")
        self.put(
            "knowledge/delta.md", "---\ntitle: Delta ledger\nsources: [x]\n---\nLedger rows.\n"
        )
        with mock.patch.object(candidates, "PROPOSE_MAX", 3):
            code, out = self.juno("triggers", "propose")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.triggers_items()), 3)
        self.assertIn("1 over the limit of 3", out)

    def test_pending_proposals_do_not_expire(self) -> None:
        self.juno("triggers", "propose")
        queue = queues.read(self.cfg, queues.RULES)
        later = candidates.number(queue[0]["t"]) + 400 * candidates.DAY
        kept = candidates.prune(queue, later)
        self.assertEqual(len(kept), 3)
        self.assertTrue(all(i["status"] == "pending" for i in kept))

    def test_refused_in_agent_context(self) -> None:
        code, out = self.juno("triggers", "propose", env={"CLAUDE_CODE_AGENT_ID": "sub-1"})
        self.assertNotEqual(code, 0)
        self.assertRegex(out, re.compile("agent context|not an interactive", re.I))
        self.assertEqual(self.triggers_items(), [])


def note(title: str, body: str) -> str:
    return f"---\ntitle: {title}\nsources: [x]\n---\n{body}\n"


class Noise(WriteCase):
    """#36 criteria 13-15: dates, ticket ids, vault-wide tokens and generic words are not proposed."""

    def triggers_items(self) -> list[queues.Item]:
        return [i for i in queues.read(self.cfg, queues.RULES) if candidates.is_triggers(i)]

    def test_criterion_13_dates_and_ticket_ids_are_never_proposed(self) -> None:
        self.put(
            "knowledge/ledger.md",
            note(
                "Ledger rotation",
                "On 2026-11-15 ABC-123456 changed the ledger-rotation. 2026-11-15 again, "
                "ABC-123456 again, ledger-rotation again.",
            ),
        )
        code, out = self.juno("triggers", "propose")
        self.assertEqual(code, 0, out)
        (item,) = self.triggers_items()
        self.assertIn("ledger-rotation", item["triggers"])
        for token in ("2026-11-15", "abc-123456", "ABC-123456"):
            self.assertNotIn(token, item["triggers"])

    def test_configured_jira_prefix_is_a_ticket(self) -> None:
        self.assertFalse(triggerprop.usable("kx-12", triggerprop.Vocab(("KX",)), "kx-12"))
        self.assertTrue(triggerprop.usable("kx-12"))

    def test_criterion_14_a_token_in_most_files_is_not_proposed(self) -> None:
        for n in range(8):
            self.put(f"knowledge/plain{n}.md", note(f"Plain {n}", "The shared-repo layout."))
        self.put(
            "knowledge/cache.md",
            note("Cache keys", "The shared-repo uses jb-cache-key; jb-cache-key rotates."),
        )
        code, out = self.juno("triggers", "propose")
        self.assertEqual(code, 0, out)
        cache = next(i for i in self.triggers_items() if i["file"] == "knowledge/cache.md")
        self.assertIn("jb-cache-key", cache["triggers"])
        self.assertNotIn("shared-repo", cache["triggers"])

    def test_a_tiny_vault_keeps_its_tokens(self) -> None:
        for n in range(3):
            self.put(f"knowledge/tiny{n}.md", note(f"Tiny {n}", "The shared-repo shared-repo."))
        self.assertEqual(knowledgetriggers.vocab(self.cfg).common, frozenset())
        self.assertEqual(triggerprop.common_tokens({"a": 2, "b": 1}, 3), frozenset())
        self.assertEqual(triggerprop.common_tokens({"a": 2, "b": 1}, 4), frozenset({"a"}))
        code, out = self.juno("triggers", "propose")
        self.assertEqual(code, 0, out)
        self.assertIn("3 trigger candidates queued", out)

    def test_prose_words_are_not_proposed_only_the_specific_token(self) -> None:
        words = "existing default found command reads users index links force partner"
        self.put(
            "knowledge/prose.md",
            note("Notes", f"{words} {words} {words}\nThe sync-window {words} sync-window."),
        )
        code, out = self.juno("triggers", "propose")
        self.assertEqual(code, 0, out)
        (item,) = self.triggers_items()
        self.assertEqual(item["triggers"], ["sync-window"])

    def test_turkish_prose_words_are_dropped_under_the_tr_preset(self) -> None:
        self.assertEqual(triggerprop.propose("", "sonra sonra kadar kadar", "tr"), [])
        self.assertEqual(
            triggerprop.propose("", "sonra sonra kadar kadar", "none"), ["kadar", "sonra"]
        )

    def test_criterion_15_only_generic_words_means_nothing_to_propose(self) -> None:
        self.put(
            "knowledge/generic.md",
            note("Status config", "branch commit deploy install status config branch commit."),
        )
        code, out = self.juno("triggers", "propose")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.triggers_items(), [])
        self.assertIn("1 files with nothing to propose", out)


class Paths(WriteCase):
    """#36 criteria 16-19: paths, hashes, uuids, links to other files and weak plain words."""

    def propose_for(self, rel: str) -> list[str]:
        code, out = self.juno("triggers", "propose")
        self.assertEqual(code, 0, out)
        items = [
            i
            for i in queues.read(self.cfg, queues.RULES)
            if candidates.is_triggers(i) and i["file"] == rel
        ]
        return list(items[0]["triggers"]) if items else []

    def test_criterion_16_path_ticket_hash_and_uuid_are_dropped_with_their_parts(self) -> None:
        uuid = "b9a0698f-285b-4c1e-9a3f-1d2e3f4a5b6c"
        body = (
            f"See handoffs/ABC-123456 and handoffs/ABC-123456 for commit 450435e and 450435e, "
            f"id {uuid} and {uuid}. The ledger-rotation and ledger-rotation."
        )
        self.put("knowledge/ledger.md", note("Ledger rotation", body))
        got = self.propose_for("knowledge/ledger.md")
        self.assertIn("ledger-rotation", got)
        banned = {"handoffs/abc-123456", "abc-123456", "450435e", uuid, "handoffs"}
        self.assertFalse(banned & set(got), got)

    def test_hash_rule_keeps_hex_looking_words(self) -> None:
        for word in ("450435e", "d0c67e0", "b9a0698f-285b-4c1e-9a3f-1d2e3f4a5b6c"):
            self.assertTrue(triggerprop.hash_like(word), word)
        for word in ("added", "decade", "1234567", "face"):
            self.assertFalse(triggerprop.hash_like(word), word)
        self.assertTrue(triggerprop.hash_like("facade1"))

    def test_criterion_17_vault_folders_and_origin_paths_are_dropped(self) -> None:
        body = (
            "Merge origin/develop, origin/develop, knowledge/other-topic.md, "
            "knowledge/other-topic.md, handoffs/radar and handoffs/radar. The sync-window, "
            "sync-window."
        )
        self.put("knowledge/paths.md", note("Paths", body))
        got = self.propose_for("knowledge/paths.md")
        self.assertEqual(got, ["sync-window"])

    def test_criterion_18_the_slug_of_another_file_is_not_proposed(self) -> None:
        self.put("knowledge/other-topic.md", note("Other topic", "Something else entirely."))
        body = "Links other-topic and other-topic. The sync-window, sync-window."
        self.put("knowledge/linker.md", note("Linker", body))
        got = self.propose_for("knowledge/linker.md")
        self.assertEqual(got, ["sync-window"])
        self.put("knowledge/self-topic.md", note("Self topic", "self-topic self-topic."))
        self.assertIn("self-topic", self.propose_for("knowledge/self-topic.md"))

    def test_criterion_19_a_plain_word_needs_two_mentions_and_few_other_files(self) -> None:
        for n in range(4):
            self.put(f"knowledge/auth{n}.md", note(f"Auth {n}", "The login flow."))
        self.put(
            "knowledge/once.md",
            note("Once", "The login is here. The jb-cache-key and jb-cache-key."),
        )
        got = self.propose_for("knowledge/once.md")
        self.assertIn("jb-cache-key", got)
        self.assertNotIn("login", got)
        self.put(
            "knowledge/twice.md",
            note("Twice", "The login is here and login again. The jb-cache-key and jb-cache-key."),
        )
        self.assertNotIn("login", self.propose_for("knowledge/twice.md"))

    def test_a_plain_word_twice_and_in_no_other_file_is_kept(self) -> None:
        for n in range(4):
            self.put(f"knowledge/solo{n}.md", note(f"Solo {n}", "Nothing alike."))
        self.put("knowledge/rare.md", note("Rare", "The tokenizer and the tokenizer."))
        self.assertIn("tokenizer", self.propose_for("knowledge/rare.md"))

    def test_session_end_path_shares_the_filter(self) -> None:
        vocab = triggerprop.Vocab(
            folders=frozenset({"handoffs"}), slugs=frozenset({"other-topic"}), docs={"login": 5}
        )
        text = "handoffs/radar handoffs/radar other-topic other-topic login login sync-window sync-window"
        self.assertEqual(triggerprop.propose("", text, "none", vocab), ["sync-window"])
        self.assertEqual(
            triggerprop.propose("", "other-topic other-topic", "none", vocab, "other-topic"),
            ["other-topic"],
        )


if __name__ == "__main__":
    unittest.main()
