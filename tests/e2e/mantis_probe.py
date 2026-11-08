"""Run inside the mantis worktree (`uv run python`): print how mantis wires the vault into an investigate session."""

import json
import sys
from pathlib import Path

from mantis.core.config import load
from mantis.core.runner import InvestigateSpec, Profile, Runner, SessionRequest, protected_roots


def main() -> None:
    cfg = load(Path(sys.argv[1]))
    request = SessionRequest(
        Profile.INVESTIGATE,
        role="investigate",
        model="m1",
        prompt="hello",
        budget_usd=0.5,
        investigate=InvestigateSpec("ABC-1"),
    )
    args = Runner(cfg)._investigate_args(request, Path(sys.argv[2]))
    dirs = [args[i + 1] for i, word in enumerate(args) if word == "--add-dir"]
    print(
        json.dumps(
            {
                "saga": str(cfg.paths.saga),
                "add_dirs": dirs,
                "protected": list(protected_roots(cfg)),
            }
        )
    )


if __name__ == "__main__":
    main()
