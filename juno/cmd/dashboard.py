import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping

from juno import config, paths
from juno.adapters import fs, store
from juno.core import dashboard
from juno.core.dashboard_scan import dashboard_dir
from juno.core.dashboard_text import Text


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--out", metavar="PATH", help="write the page here instead of the state dir"
    )
    parser.add_argument("--json", action="store_true", help="print the data as JSON, write nothing")
    parser.add_argument("--open", action="store_true", help="open the page in the default viewer")


def sources(cfg: config.Config) -> dashboard.Sources:
    env = cfg.env
    projects = env.get("JUNO_CLAUDE_PROJECTS") or os.path.join(cfg.claude_dir, "projects")
    budget = cfg.get_float("handoff", "daily_budget_usd")
    return dashboard.Sources(
        vault=cfg.vault,
        state=cfg.state,
        transcripts=paths.expand(projects, env),
        home=paths.home(env),
        t=Text(cfg.get_str("language", "preset")),
        ticket=dashboard.ticket_pattern(cfg.get_list("jira", "prefixes")),
        daily_budget=budget if budget > 0 else None,
    )


def opener() -> str | None:
    return shutil.which("open" if sys.platform == "darwin" else "xdg-open")


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    cfg = config.load(env)
    src = sources(cfg)
    if args.out and store.in_vault(cfg, paths.expand(args.out, env)):
        sys.stderr.write("Juno: dashboard: --out must not be inside the vault\n")
        return 2
    data, html = dashboard.generate(src, time.time())
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        return 0
    out = paths.expand(args.out, env) if args.out else dashboard_dir(src.state, "index.html")
    if not args.out:
        fs.ensure_private_dir(os.path.dirname(out))
    fs.atomic_write(out, html.encode("utf-8"), 0o600)
    print(out)
    if args.open:
        viewer = opener()
        if viewer:
            subprocess.run([viewer, out], check=False)
    return 0
