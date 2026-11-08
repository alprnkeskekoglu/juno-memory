import os
import unittest

from juno import config
from juno.adapters import repos
from juno.core.detect import Tickets
from tests.support.hooks import HookCase, git

T = Tickets(("ABC", "DEF"))


class TicketMatching(unittest.TestCase):
    def test_negatives(self) -> None:
        for text in (
            "UTF-8",
            "SHA-256",
            "CVE-2026-1234",
            "XABC-12",
            "ABC-",
            "abc-12abc",
            "ABCX-12",
        ):
            self.assertEqual(T.find(text), [], text)

    def test_positives_and_branches(self) -> None:
        self.assertEqual(T.find("fix ABC-333333 and ABC-1, again Abc-1"), ["ABC-333333", "ABC-1"])
        self.assertEqual(T.branch_ticket("feature/ABC-444444-refund"), "ABC-444444")
        for branch in ("develop", "master", "main", "HEAD", "", None):
            self.assertIsNone(T.branch_ticket(branch))

    def test_no_prefixes_means_no_tickets_anywhere(self) -> None:
        none = Tickets()
        self.assertFalse(none.enabled)
        self.assertEqual(none.find("ABC-1"), [])
        self.assertEqual(none.command_tickets("/x ABC-1"), [])
        self.assertEqual(none.split_dirname("repo-ABC-1"), ("repo-ABC-1", None))
        self.assertIsNone(none.branch_ticket("feature/ABC-1"))

    def test_split_dirname(self) -> None:
        self.assertEqual(T.split_dirname("billing-api-ABC-111111"), ("billing-api", "ABC-111111"))
        self.assertEqual(T.split_dirname("honey_badger_abc-7"), ("honey_badger", "ABC-7"))
        self.assertEqual(T.split_dirname("plain"), ("plain", None))


class Detection(HookCase):
    def cfg(self) -> config.Config:
        return config.load(dict(self.env))

    def repos_for(self, *paths: str) -> dict[str, str | None]:
        return repos.repos_for(self.cfg(), list(paths))

    def test_repo_from_root_with_suffix(self) -> None:
        repo = self.make_repo("billing-api-ABC-111111")
        self.assertEqual(
            self.repos_for(os.path.join(repo, "src", "a.py")), {"billing-api": "ABC-111111"}
        )

    def test_branch_ticket_wins_over_nothing(self) -> None:
        repo = self.make_repo("alpha", "feature/ABC-9")
        self.assertEqual(self.repos_for(repo), {"alpha": "ABC-9"})

    def test_worktree_resolves_to_main_repo(self) -> None:
        main = self.make_repo("ash")
        tree = os.path.join(self.work, "ash-wt")
        git(main, "worktree", "add", "-q", "--orphan", "-b", "feature/ABC-77", tree)
        self.assertEqual(self.repos_for(tree), {"ash": "ABC-77"})

    def test_paths_outside_roots_vault_and_state_are_not_repos(self) -> None:
        os.makedirs(self.sb.state, exist_ok=True)
        self.assertEqual(
            self.repos_for(
                self.work,
                self.sb.vault,
                self.sb.state,
                "/",
                os.path.join(self.projects, "ghost", "a.py"),
            ),
            {},
        )

    def test_git_dir_and_git_file_under_second_root(self) -> None:
        os.makedirs(os.path.join(self.extra, "maestro", ".git"))
        self.assertEqual(
            self.repos_for(os.path.join(self.extra, "maestro", "a.py")), {"maestro": None}
        )
        self.assertEqual(repos.repo_dir(self.cfg(), "maestro"), os.path.join(self.extra, "maestro"))
        os.makedirs(os.path.join(self.extra, "filegit"))
        with open(os.path.join(self.extra, "filegit", ".git"), "w", encoding="utf-8") as handle:
            handle.write("gitdir: /nowhere\n")
        self.assertEqual(self.repos_for(os.path.join(self.extra, "filegit")), {"filegit": None})

    def test_non_git_folder_loose_files_and_hidden_are_not_repos(self) -> None:
        os.makedirs(os.path.join(self.extra, "backups"))
        os.makedirs(os.path.join(self.extra, ".hidden", ".git"))
        with open(os.path.join(self.extra, "CLAUDE.md"), "w", encoding="utf-8") as handle:
            handle.write("x\n")
        paths = [
            os.path.join(self.extra, "backups", "a"),
            os.path.join(self.extra, ".hidden", "x"),
            os.path.join(self.extra, "CLAUDE.md"),
            self.extra,
        ]
        self.assertEqual(self.repos_for(*paths), {})
        self.assertTrue(repos.plain_folder(self.cfg(), "backups"))
        self.assertIsNone(repos.repo_dir(self.cfg(), "backups"))

    def test_exclude_list(self) -> None:
        os.makedirs(os.path.join(self.extra, "triage", ".git"))
        self.assertEqual(self.repos_for(os.path.join(self.extra, "triage", "x")), {"triage": None})
        self.set_config('[repos]\nexclude = ["triage"]\n')
        self.assertEqual(self.repos_for(os.path.join(self.extra, "triage", "x")), {})
        self.assertIsNone(repos.repo_dir(self.cfg(), "triage"))

    def test_first_root_wins_in_config_order(self) -> None:
        self.make_repo("dup", root=self.projects)
        self.make_repo("dup", root=self.extra)
        self.assertEqual(repos.repo_dir(self.cfg(), "dup"), os.path.join(self.projects, "dup"))

    def test_worktree_of_the_vault_is_not_a_repo(self) -> None:
        git(self.sb.vault, "init", "-q", "-b", "main")
        tree = os.path.join(self.work, "vault-wt")
        git(self.sb.vault, "worktree", "add", "-q", "--orphan", "-b", "x", tree)
        self.assertEqual(self.repos_for(tree), {})

    def test_without_jira_branch_tickets_are_ignored(self) -> None:
        self.disable_tickets()
        repo = self.make_repo("alpha-ABC-5", "feature/ABC-9")
        self.assertEqual(self.repos_for(repo), {"alpha-ABC-5": None})

    def test_overview_dir_is_the_first_root_by_default(self) -> None:
        self.assertTrue(repos.is_overview_dir(self.cfg(), self.projects))
        self.assertFalse(repos.is_overview_dir(self.cfg(), self.extra))


if __name__ == "__main__":
    unittest.main()
