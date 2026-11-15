"""Doctor check for repos named in cards that have no vault profile."""

from collections.abc import Mapping

Check = tuple[str, str, str]


def profile_checks(counts: Mapping[str, int], profiles: set[str], min_cards: int) -> list[Check]:
    missing = {r: n for r, n in counts.items() if r not in profiles}
    shown = sorted(
        (r for r, n in missing.items() if n >= min_cards), key=lambda r: (-missing[r], r)
    )
    out: list[Check] = []
    if shown:
        listed = ", ".join(f"{r} ({missing[r]})" for r in shown)
        out.append(("WARN", "profiles", "repos in cards without a profile: " + listed))
    else:
        out.append(("PASS", "profiles", "every repo in cards has a profile"))
    few = len(missing) - len(shown)
    if few:
        noun = "repo in fewer" if few == 1 else "repos in fewer"
        verb = "has" if few == 1 else "have"
        out.append(("INFO", "profiles", f"{few} {noun} than {min_cards} cards {verb} no profile"))
    return out
