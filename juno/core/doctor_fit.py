from collections.abc import Mapping

from juno.core.vaultfmt import injected_rules, section

Check = tuple[str, str, str]


def pack_fit_checks(
    profiles: Mapping[str, str], global_text: str, pack_chars: int, subagent_chars: int
) -> list[Check]:
    out: list[Check] = []
    for name, text in sorted(profiles.items()):
        for sec in ("Gotchas", "How to run"):
            size = len(section(text, sec))
            if size > pack_chars:
                said = f"projects/{name}.md {sec} is {size} chars but the pack budget is "
                said += f"{pack_chars}; the oldest lines are cut, so run "
                said += f"`juno consolidate projects/{name}.md --section {sec}`"
                out.append(("WARN", "pack fit", said))
    size = len(injected_rules(global_text))
    if size > subagent_chars:
        said = f"rules/global.md injects {size} chars but the subagent budget is "
        said += f"{subagent_chars}; subagents see shortened or only the newest rules"
        out.append(("WARN", "pack fit", said))
    return out
