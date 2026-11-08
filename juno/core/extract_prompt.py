from juno.core.digest import clip
from juno.core.extract import MARKER
from juno.core.redact import redact

STATE_CHARS = 5000
STATE_FLOOR = 2000


def instructions(keys: list[str], bullets: list[str]) -> list[str]:
    quoted = [f'"{k}"' for k in keys]
    listed = ", ".join(quoted[:-1]) + " and " + quoted[-1]
    return [
        "",
        f"After the card, output one line `{MARKER}`, then one JSON object (no code fence) with "
        f"the keys {listed}. Use empty lists when nothing qualifies; most segments have none.",
        '- The digest\'s "Learning moments" section (when present) is the primary source for '
        '"knowledge" (what was found wrong and the verified correct behaviour, with evidence '
        'copied from the digest) and for "rules" (a user correction there may be a rule '
        "candidate, but only with a verbatim quote from a typed prompt, as below).",
        *bullets,
    ]


def profile_status(repos: list[str], exists: set[str], creatable: set[str]) -> str:
    parts = []
    for repo in repos:
        if repo in exists:
            parts.append(f"{repo} (exists)")
        elif repo in creatable:
            parts.append(f"{repo} (none yet, created on first use)")
        else:
            parts.append(f"{repo} (none, do not use)")
    return ", ".join(parts) or "-"


def vault_state(body: list[str], tail: str) -> str:
    room = max(STATE_CHARS - len(tail) - 2, STATE_FLOOR) if tail else STATE_CHARS
    text = clip(redact("\n\n".join(body)), room)
    return text + "\n\n" + tail if tail else text
