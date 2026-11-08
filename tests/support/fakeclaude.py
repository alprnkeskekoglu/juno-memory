import json
import os
import stat
import sys

SCRIPT = """#!{python}
import json, os, sys
prompt = sys.stdin.read()
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    keys = ("JUNO_DISABLE", "JUNO_CARD_TOKEN", "CLAUDE_CODE_SESSION_ID")
    with open(log, "w", encoding="utf-8") as handle:
        json.dump(
            {{
                "argv": sys.argv[1:],
                "prompt": prompt,
                "cwd": os.getcwd(),
                "env": {{k: os.environ.get(k) for k in keys}},
            }},
            handle,
        )
out = os.environ.get("FAKE_CLAUDE_OUT")
if out and os.path.exists(out):
    with open(out, encoding="utf-8") as handle:
        sys.stdout.write(handle.read())
else:
    print(json.dumps({{"is_error": True, "subtype": "error", "result": "fake claude: no output"}}))
"""


def install(root: str) -> dict[str, str]:
    binary = os.path.join(root, "fake-claude")
    with open(binary, "w", encoding="utf-8") as handle:
        handle.write(SCRIPT.format(python=sys.executable))
    os.chmod(binary, os.stat(binary).st_mode | stat.S_IXUSR)
    return {
        "JUNO_CARD_CLAUDE": binary,
        "FAKE_CLAUDE_LOG": os.path.join(root, "fake-claude.log"),
        "FAKE_CLAUDE_OUT": os.path.join(root, "fake-claude.out"),
    }


def answer(env: dict[str, str], text: str, cost: float = 0.05) -> None:
    doc = {"subtype": "success", "is_error": False, "result": text, "total_cost_usd": cost}
    with open(env["FAKE_CLAUDE_OUT"], "w", encoding="utf-8") as handle:
        json.dump(doc, handle)


def call_log(env: dict[str, str]) -> dict[str, object]:
    try:
        with open(env["FAKE_CLAUDE_LOG"], encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}
