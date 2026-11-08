import os
import subprocess
import unittest
from unittest import mock

from juno.adapters import git
from tests.support import SandboxCase


class CleanEnv(SandboxCase):
    def test_git_variables_are_stripped(self) -> None:
        env = git.clean_env(
            {"GIT_DIR": "/decoy", "GIT_INDEX_FILE": "/x", "PATH": "/bin", "HOME": "/h"}
        )
        self.assertEqual(env, {"PATH": "/bin", "HOME": "/h", "GIT_OPTIONAL_LOCKS": "0"})

    def test_run_ignores_a_decoy_git_dir(self) -> None:
        repo = os.path.join(self.sb.root, "real")
        os.makedirs(repo)
        subprocess.run(
            ["git", "-C", repo, "init", "-q", "-b", "feature/x"], check=True, capture_output=True
        )
        decoy = os.path.join(self.sb.root, "decoy")
        os.makedirs(decoy)
        leak = {"GIT_DIR": decoy, "GIT_INDEX_FILE": os.path.join(decoy, "index")}
        with mock.patch.dict(os.environ, leak):
            self.assertEqual(git.run(["symbolic-ref", "--short", "HEAD"], repo), "feature/x")


if __name__ == "__main__":
    unittest.main()
