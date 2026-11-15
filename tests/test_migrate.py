import argparse
import os
import unittest

from juno.adapters import migratesteps
from juno.cmd import migrate as cmd
from juno.core import migrate
from tests.support import REPO, SandboxCase


class Core(unittest.TestCase):
    def test_schema_read_write(self) -> None:
        text = "schema_version = 1\ncreated = 2026-01-01\n"
        self.assertEqual(migrate.read_schema(text), 1)
        self.assertEqual(
            migrate.write_schema(text, 2), "schema_version = 2\ncreated = 2026-01-01\n"
        )
        self.assertIsNone(migrate.read_schema("created = 2026-01-01\n"))
        self.assertTrue(migrate.write_schema("a = 1\n", 3).startswith("schema_version = 3\n"))

    def test_plan_is_one_version_at_a_time(self) -> None:
        steps = {1: lambda v: None, 2: lambda v: None}
        self.assertEqual(migrate.plan(1, 3, steps), [1, 2])
        self.assertEqual(migrate.plan(3, 3, steps), [])
        with self.assertRaises(ValueError):
            migrate.plan(1, 4, steps)

    def test_code_schema_is_four_with_three_steps(self) -> None:
        self.assertEqual(migrate.CODE_SCHEMA_VERSION, 4)
        self.assertEqual(sorted(migratesteps.STEPS), [1, 2, 3])
        self.assertEqual(migrate.plan(1, 4, migratesteps.STEPS), [1, 2, 3])

    def test_decision_files_merge_without_duplicates(self) -> None:
        head = "---\ntopic: ABC-6\nupdated: 2026-05-03\nvalid_from: 2026-05-03\n---\n"
        upper = head + "## Current\n\n- Upper six (user p2, 2026-05-04)\n\n## History\n"
        lower = (
            head + "## Current\n\n- Lower six (user p1, 2026-05-03)\n"
            "- Upper six (user p2, 2026-05-04)\n\n"
            "## History\n- Old six (user p1, 2026-04-01)\n"
        )
        merged = migrate.merge_decision_files(upper, lower)
        for text in ("Lower six", "Upper six", "Old six"):
            self.assertEqual(merged.count(text), 1, text)
        self.assertIn("Old six", merged.split("## History")[1])
        self.assertEqual(migrate.merge_decision_files(merged, lower), merged)

    def test_stale_exception_is_added_once_and_only_to_the_old_sentence(self) -> None:
        old = open(os.path.join(REPO, "vault-template", "core.md"), encoding="utf-8").read()
        old = old.replace(migrate.PRIVACY_NEW, migrate.PRIVACY_OLD)
        self.assertFalse(migrate.privacy_has_exception(old))
        new = migrate.add_stale_exception(old)
        self.assertTrue(migrate.privacy_has_exception(new))
        self.assertEqual(migrate.add_stale_exception(new), new)
        edited = old.replace(migrate.PRIVACY_OLD, "Write memory only when I say so.")
        self.assertEqual(migrate.add_stale_exception(edited), edited)
        self.assertFalse(migrate.privacy_has_exception(edited))
        self.assertEqual(old.replace(migrate.PRIVACY_OLD, ""), new.replace(migrate.PRIVACY_NEW, ""))


class Command(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        code, _ = self.sb.cli("init", *self.sb.install_args())
        self.assertEqual(code, 0)
        self.toml = os.path.join(self.sb.vault, "juno-brain.toml")

    def set_schema(self, version: int) -> None:
        with open(self.toml, encoding="utf-8") as handle:
            text = handle.read()
        with open(self.toml, "w", encoding="utf-8") as handle:
            handle.write(migrate.write_schema(text, version))

    def args(self, **kw: bool) -> argparse.Namespace:
        return argparse.Namespace(dry_run=kw.get("dry_run", False))

    def schema(self) -> int | None:
        with open(self.toml, encoding="utf-8") as handle:
            return migrate.read_schema(handle.read())

    def run_cmd(self, steps: dict[int, migrate.Step], target: int, **kw: bool) -> int:
        return cmd.run(self.args(**kw), self.sb.env, steps, target)

    def test_up_to_date(self) -> None:
        code, out = self.sb.cli("migrate")
        self.assertEqual(code, 0)
        self.assertIn("nothing to do", out)

    def test_steps_apply_in_order_with_backup(self) -> None:
        self.set_schema(1)
        seen: list[int] = []

        def step1(vault: str) -> None:
            seen.append(1)
            with open(os.path.join(vault, "notes", "from-step-1.md"), "w") as handle:
                handle.write("x\n")

        def step2(vault: str) -> None:
            seen.append(2)
            self.assertEqual(self.schema(), 2)

        self.assertEqual(self.run_cmd({1: step1, 2: step2}, 3), 0)
        self.assertEqual(seen, [1, 2])
        self.assertEqual(self.schema(), 3)
        backups = os.listdir(os.path.join(self.sb.state, "backups"))
        self.assertEqual(sorted(b.split("-v")[1] for b in backups), ["1", "2"])
        first = next(b for b in backups if b.endswith("-v1"))
        self.assertFalse(
            os.path.exists(os.path.join(self.sb.state, "backups", first, "notes", "from-step-1.md"))
        )

    def test_failed_step_leaves_schema(self) -> None:
        self.set_schema(1)

        def boom(vault: str) -> None:
            raise RuntimeError("step failed")

        with self.assertRaises(RuntimeError):
            self.run_cmd({1: boom}, 2)
        self.assertEqual(self.schema(), 1)

    def test_dry_run_changes_nothing(self) -> None:
        self.set_schema(1)
        called: list[int] = []
        self.assertEqual(self.run_cmd({1: lambda v: called.append(1)}, 2, dry_run=True), 0)
        self.assertEqual((called, self.schema()), ([], 1))
        self.assertFalse(os.path.exists(os.path.join(self.sb.state, "backups")))

    def test_missing_step_refused(self) -> None:
        self.set_schema(1)
        self.assertEqual(self.run_cmd({}, 2), 2)

    def test_real_step_adds_the_exception_to_a_schema_one_vault(self) -> None:
        core = os.path.join(self.sb.vault, "core.md")
        with open(core, encoding="utf-8") as handle:
            text = handle.read()
        with open(core, "w", encoding="utf-8") as handle:
            handle.write(text.replace(migrate.PRIVACY_NEW, migrate.PRIVACY_OLD))
        self.set_schema(1)
        self.assertEqual(self.sb.cli("migrate")[0], 0)
        self.assertEqual(self.schema(), 4)
        with open(core, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), text)
        self.assertEqual(self.sb.cli("migrate")[0], 0)
        with open(core, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), text)

    def read(self, rel: str) -> str:
        with open(os.path.join(self.sb.vault, rel), encoding="utf-8") as handle:
            return handle.read()

    def write(self, rel: str, text: str) -> None:
        with open(os.path.join(self.sb.vault, rel), "w", encoding="utf-8") as handle:
            handle.write(text)

    def make_schema_two(self) -> tuple[str, str]:
        rules = self.read(os.path.join("protocol", "rules.md"))
        core = self.read("core.md")
        self.assertIn(migrate.RULES_QUOTE_NEW, rules)
        self.assertIn(migrate.CAPS_NEW, core)
        self.write(
            os.path.join("protocol", "rules.md"),
            rules.replace(migrate.RULES_QUOTE_NEW, migrate.RULES_QUOTE_OLD),
        )
        self.write("core.md", core.replace(migrate.CAPS_NEW, migrate.CAPS_OLD))
        self.set_schema(2)
        return rules, core

    def test_step_two_rewrites_quote_rule_and_cap_sentence(self) -> None:
        rules, core = self.make_schema_two()
        self.assertEqual(self.sb.cli("migrate")[0], 0)
        self.assertEqual(self.schema(), 4)
        self.assertEqual(self.read(os.path.join("protocol", "rules.md")), rules)
        self.assertEqual(self.read("core.md"), core)
        backups = os.listdir(os.path.join(self.sb.state, "backups"))
        first = next(b for b in backups if b.endswith("-v2"))
        old = os.path.join(self.sb.state, "backups", first, "protocol", "rules.md")
        with open(old, encoding="utf-8") as handle:
            self.assertIn(migrate.RULES_QUOTE_OLD, handle.read())

    def test_step_two_is_idempotent(self) -> None:
        self.make_schema_two()
        steps = {2: migratesteps.quote_and_cap_text}
        migratesteps.quote_and_cap_text(self.sb.vault)
        once = (self.read(os.path.join("protocol", "rules.md")), self.read("core.md"))
        self.set_schema(2)
        self.assertEqual(self.run_cmd(steps, 3), 0)
        twice = (self.read(os.path.join("protocol", "rules.md")), self.read("core.md"))
        self.assertEqual(once, twice)

    def test_step_two_leaves_edited_lines_alone(self) -> None:
        self.make_schema_two()
        rules = self.read(os.path.join("protocol", "rules.md")).replace(
            migrate.RULES_QUOTE_OLD, "- The quote: whatever I typed."
        )
        core = self.read("core.md").replace(migrate.CAPS_OLD, "Stay small.")
        self.write(os.path.join("protocol", "rules.md"), rules)
        self.write("core.md", core)
        self.assertEqual(self.sb.cli("migrate")[0], 0)
        self.assertEqual(self.schema(), 4)
        self.assertEqual(self.read(os.path.join("protocol", "rules.md")), rules)
        self.assertEqual(self.read("core.md"), core)

    def make_schema_three(self) -> None:
        self.set_schema(3)

    def test_step_three_moves_task_decisions_into_the_decision_file(self) -> None:
        self.make_schema_three()
        self.write(
            "tasks/ABC-1.md",
            "---\ntitle: T\n---\n\n## Decisions\n"
            "- Use v2 API (user pid00001, 2026-05-01)\n"
            "- Retry three times (user pid00002, 2026-05-02)\n\n## Notes\n- keep\n",
        )
        self.write(
            "decisions/ABC-1.md",
            "---\ntopic: ABC-1\nupdated: 2026-05-03\nvalid_from: 2026-05-03\n---\n"
            "## Current\n\n- Use v2 API (user pid00009, 2026-05-03)\n\n## History\n",
        )
        self.write(
            "tasks/ABC-2.md",
            "---\ntitle: U\n---\n## Decisions\n- Only here (user pid1, 2026-05-04)\n",
        )
        self.assertEqual(self.sb.cli("migrate")[0], 0)
        self.assertEqual(self.schema(), 4)
        task = self.read("tasks/ABC-1.md")
        self.assertNotIn("Decisions", task)
        self.assertIn("## Notes\n- keep", task)
        current = self.read("decisions/ABC-1.md")
        self.assertEqual(current.count("Use v2 API"), 1)
        self.assertIn("- Retry three times (user pid00002, 2026-05-02)", current)
        two = self.read("decisions/ABC-2.md")
        self.assertIn("topic: ABC-2", two)
        self.assertIn("## Current\n\n- Only here (user pid1, 2026-05-04)", two)
        self.assertNotIn("Decisions", self.read("tasks/ABC-2.md"))
        before = (task, current, two)
        migratesteps.ticket_decisions(self.sb.vault)
        self.assertEqual(
            before,
            (
                self.read("tasks/ABC-1.md"),
                self.read("decisions/ABC-1.md"),
                self.read("decisions/ABC-2.md"),
            ),
        )

    def test_step_three_renames_lowercase_ticket_decision_files(self) -> None:
        self.make_schema_three()
        head = "---\ntopic: %s\nupdated: 2026-05-03\nvalid_from: 2026-05-03\n---\n"
        self.write(
            "decisions/abc-5.md",
            head % "abc-5" + "## Current\n\n- Lower only (user p1, 2026-05-03)\n\n## History\n",
        )
        self.write(
            "decisions/storage.md",
            head % "storage" + "## Current\n\n- keep (user p1, 2026-05-03)\n",
        )
        self.assertEqual(self.sb.cli("migrate")[0], 0)
        names = os.listdir(os.path.join(self.sb.vault, "decisions"))
        self.assertIn("ABC-5.md", names)
        self.assertNotIn("abc-5.md", names)
        self.assertIn("storage.md", names)
        five = self.read("decisions/ABC-5.md")
        self.assertIn("topic: ABC-5", five)
        self.assertIn("Lower only", five)
        self.assertEqual(self.sb.cli("migrate")[0], 0)
        self.assertEqual(self.read("decisions/ABC-5.md"), five)

    def test_newer_vault_refused(self) -> None:
        with open(self.toml, "w", encoding="utf-8") as handle:
            handle.write("schema_version = 9\n")
        code, out = self.sb.cli("migrate")
        self.assertEqual(code, 2)
        self.assertIn("newer", out)

    def test_no_vault(self) -> None:
        self.sb.env["JUNO_VAULT"] = os.path.join(self.sb.root, "elsewhere")
        self.assertEqual(self.sb.cli("migrate")[0], 2)


if __name__ == "__main__":
    unittest.main()


class Cards(SandboxCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.sb.cli("init", *self.sb.install_args())[0], 0)

    def card(self, seg: str, repos: str) -> str:
        return (
            f"## 2026-11-01 10:00 · - · work · {seg}\nRepos: {repos}\nTickets: -\n"
            "Did it.\nLearned: none\n\n"
        )

    def read(self, name: str) -> str:
        with open(os.path.join(self.sb.vault, "handoffs", name), encoding="utf-8") as handle:
            return handle.read()

    def write(self, name: str, text: str) -> None:
        with open(os.path.join(self.sb.vault, "handoffs", name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_cards_drops_copies_outside_the_repos_the_card_names(self) -> None:
        keep = "# Handoffs\n\n"
        self.write("juno.md", keep + self.card("aaaa1111", "juno") + self.card("cccc3333", "juno"))
        self.write("web.md", keep + self.card("aaaa1111", "juno") + self.card("bbbb2222", "web"))
        self.write("api.md", keep + self.card("aaaa1111", "juno"))
        os.makedirs(os.path.join(self.sb.vault, "projects"), exist_ok=True)
        with open(os.path.join(self.sb.vault, "projects", "api.md"), "w", encoding="utf-8") as h:
            h.write("# api\n")
        self.write("x.md", keep + self.card("dddd4444", "x, y"))
        self.write("y.md", keep + self.card("dddd4444", "x, y"))
        self.assertEqual(self.sb.cli("migrate", "--cards", "--dry-run")[0], 0)
        self.assertIn("aaaa1111", self.read("web.md"))
        self.assertEqual(self.sb.cli("migrate", "--cards")[0], 0)
        self.assertEqual(self.read("juno.md").count("aaaa1111"), 1)
        self.assertNotIn("aaaa1111", self.read("web.md"))
        self.assertNotIn("aaaa1111", self.read("api.md"))
        self.assertIn("bbbb2222", self.read("web.md"))
        self.assertIn("cccc3333", self.read("juno.md"))
        self.assertIn("dddd4444", self.read("x.md"))
        self.assertIn("dddd4444", self.read("y.md"))
        after = {n: self.read(n) for n in os.listdir(os.path.join(self.sb.vault, "handoffs"))}
        self.assertEqual(self.sb.cli("migrate", "--cards")[0], 0)
        self.assertEqual(after, {n: self.read(n) for n in after})

    def test_cards_keeps_one_copy_when_none_is_filed_under_a_named_repo(self) -> None:
        for name in ("p", "q"):
            text = self.card("aaaa1111", "other") + self.card(name * 8, name)
            self.write(f"{name}.md", "# H\n\n" + text)
        self.assertEqual(self.sb.cli("migrate", "--cards")[0], 0)
        found = sum(self.read(n).count("aaaa1111") for n in ("p.md", "q.md", "other.md"))
        self.assertEqual(found, 1)
        self.assertIn("aaaa1111", self.read("other.md"))

    def profiles(self, *names: str) -> None:
        os.makedirs(os.path.join(self.sb.vault, "projects"), exist_ok=True)
        for name in names:
            with open(os.path.join(self.sb.vault, "projects", f"{name}.md"), "w") as handle:
                handle.write(f"# {name}\n")

    def misfiled_warnings(self) -> int:
        return self.sb.cli("doctor")[1].count("misfiled cards")

    def test_cards_moves_a_misfiled_card_to_the_repo_it_names(self) -> None:
        self.profiles("alpha", "beta")
        card = self.card("aaaaaaaa", "beta")
        self.write("alpha.md", "# Handoffs · alpha\n\n" + card)
        self.assertEqual(self.misfiled_warnings(), 1)
        before = self.read("alpha.md")
        code, out = self.sb.cli("migrate", "--cards", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would move", out)
        self.assertEqual(self.read("alpha.md"), before)
        self.assertFalse(os.path.exists(os.path.join(self.sb.vault, "handoffs", "beta.md")))
        code, out = self.sb.cli("migrate", "--cards")
        self.assertEqual(code, 0)
        self.assertIn("moved 1 card from handoffs/alpha.md to handoffs/beta.md", out)
        self.assertTrue(self.read("beta.md").startswith("# Handoffs · beta\n"))
        self.assertIn(card.rstrip("\n"), self.read("beta.md"))
        self.assertNotIn("aaaaaaaa", self.read("alpha.md"))
        self.assertEqual(self.misfiled_warnings(), 0)
        after = {n: self.read(n) for n in ("alpha.md", "beta.md")}
        self.assertEqual(self.sb.cli("migrate", "--cards")[0], 0)
        self.assertEqual(after, {n: self.read(n) for n in after})

    def test_cards_move_goes_to_the_first_repo_and_keeps_time_order(self) -> None:
        self.profiles("alpha", "beta", "gamma")
        early = self.card("bbbbbbbb", "beta").replace("10:00", "09:00")
        late = self.card("cccccccc", "beta").replace("10:00", "11:00")
        self.write("beta.md", "# Handoffs · beta\n\n" + early + late)
        self.write("alpha.md", "# H\n\n" + self.card("aaaaaaaa", "beta, gamma"))
        self.assertEqual(self.sb.cli("migrate", "--cards")[0], 0)
        text = self.read("beta.md")
        self.assertLess(text.index("bbbbbbbb"), text.index("aaaaaaaa"))
        self.assertLess(text.index("aaaaaaaa"), text.index("cccccccc"))
        self.assertFalse(os.path.exists(os.path.join(self.sb.vault, "handoffs", "gamma.md")))

    def test_cards_move_into_a_file_that_has_the_segment_is_a_copy_drop(self) -> None:
        self.profiles("alpha", "beta")
        self.write("beta.md", "# H\n\n" + self.card("aaaaaaaa", "beta"))
        self.write("alpha.md", "# H\n\n" + self.card("aaaaaaaa", "beta"))
        self.assertEqual(self.sb.cli("migrate", "--cards")[0], 0)
        self.assertEqual(self.read("beta.md").count("aaaaaaaa"), 1)
        self.assertNotIn("aaaaaaaa", self.read("alpha.md"))

    def test_cards_move_leaves_ticket_and_label_files_and_ticketless_cards_alone(self) -> None:
        self.profiles("alpha", "beta")
        self.write("ABC-1.md", "# H\n\n" + self.card("aaaaaaaa", "beta"))
        self.write("topic.md", "# H\n\n" + self.card("bbbbbbbb", "beta"))
        self.write("alpha.md", "# H\n\n" + self.card("cccccccc", "-"))
        before = {n: self.read(n) for n in ("ABC-1.md", "topic.md", "alpha.md")}
        self.assertEqual(self.sb.cli("migrate", "--cards")[0], 0)
        self.assertEqual(before, {n: self.read(n) for n in before})

    def test_cards_move_needs_no_profile_for_the_named_repo(self) -> None:
        self.profiles("alpha")
        self.write("alpha.md", "# H\n\n" + self.card("aaaaaaaa", "beta"))
        self.assertEqual(self.misfiled_warnings(), 1)
        self.assertEqual(self.sb.cli("migrate", "--cards")[0], 0)
        self.assertIn("aaaaaaaa", self.read("beta.md"))
        self.assertEqual(self.misfiled_warnings(), 0)

    def five_shapes(self) -> dict[str, str]:
        """Every shape of #25: named + extras, `-` cards, and a card filed once in each of 4 repos."""
        self.profiles("alpha")
        keep = "# H\n\n"
        c = self.card
        self.write(
            "alpha.md",
            keep + c("aaaa0001", "alpha") + c("aaaa0002", "alpha") + c("aaaa0003", "alpha"),
        )
        self.write("notes.md", keep + c("aaaa0001", "alpha") + c("aaaa0002", "alpha"))
        self.write("ideas.md", keep + c("aaaa0002", "alpha") + c("aaaa0003", "alpha"))
        self.write("todo.md", keep + c("aaaa0002", "alpha") + c("aaaa0003", "alpha"))
        self.write("misc.md", keep + c("aaaa0004", "-"))
        self.write("scratch.md", keep + c("aaaa0004", "-") + c("aaaa0005", "-"))
        self.write("zzz.md", keep + c("aaaa0005", "-"))
        for repo in ("r1", "r2", "r3", "r4"):
            self.write(f"{repo}.md", keep + c("aaaa0006", "r1, r2, r3, r4"))
        return {n: self.read(n) for n in os.listdir(os.path.join(self.sb.vault, "handoffs"))}

    def copy_warnings(self) -> list[str]:
        out = self.sb.cli("doctor")[1]
        return [line for line in out.splitlines() if "card copies" in line]

    def test_cards_doctor_and_migrate_agree_on_every_shape(self) -> None:
        before = self.five_shapes()
        warned = self.copy_warnings()
        self.assertEqual(len(warned), 5)
        self.assertFalse(any("aaaa0006" in line for line in warned))
        code, out = self.sb.cli("migrate", "--cards", "--dry-run")
        self.assertEqual(code, 0)
        for rel, seg in [
            ("notes.md", "aaaa0001"), ("notes.md", "aaaa0002"), ("ideas.md", "aaaa0002"),
            ("todo.md", "aaaa0002"), ("ideas.md", "aaaa0003"), ("todo.md", "aaaa0003"),
            ("scratch.md", "aaaa0004"), ("zzz.md", "aaaa0005"),
        ]:  # fmt: skip
            line = next((x for x in out.splitlines() if f"handoffs/{rel}" in x), "")
            self.assertIn("would drop", line)
            self.assertIn(seg, line)
        self.assertEqual({n: self.read(n) for n in before}, before)
        self.assertEqual(self.sb.cli("migrate", "--cards")[0], 0)
        self.assertEqual(self.copy_warnings(), [])
        after = {n: self.read(n) for n in os.listdir(os.path.join(self.sb.vault, "handoffs"))}
        repos = {"aaaa0004": "-", "aaaa0005": "-", "aaaa0006": "r1, r2, r3, r4"}
        for i in range(1, 7):
            seg = f"aaaa000{i}"
            held = [n for n, t in after.items() if seg in t]
            self.assertGreaterEqual(len(held), 1)
            for n in held:  # the surviving copy is byte-identical to what was filed
                self.assertIn(self.card(seg, repos.get(seg, "alpha")), after[n])
                self.assertIn(self.card(seg, repos.get(seg, "alpha")), before[n])
        self.assertEqual([n for n, t in after.items() if "aaaa0001" in t], ["alpha.md"])
        self.assertEqual([n for n, t in after.items() if "aaaa0004" in t], ["misc.md"])
        self.assertEqual([n for n, t in after.items() if "aaaa0005" in t], ["scratch.md"])
        self.assertEqual(len([n for n, t in after.items() if "aaaa0006" in t]), 4)
        self.assertEqual(self.sb.cli("migrate", "--cards")[0], 0)
        self.assertEqual(after, {n: self.read(n) for n in after})

    def test_cards_four_repo_card_is_left_alone(self) -> None:
        for repo in ("r1", "r2", "r3", "r4"):
            self.write(f"{repo}.md", "# H\n\n" + self.card("aaaa0006", "r1, r2, r3, r4"))
        self.assertEqual(self.copy_warnings(), [])
        self.assertIn("no extra copies", self.sb.cli("migrate", "--cards", "--dry-run")[1])
