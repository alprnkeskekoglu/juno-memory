import re

from juno.core.fold import fold

# An opinion or stance on a topic, not a working behaviour. Matched on the line text only: the
# quote is the user's own words and may state opinions.
STANCE_RE = re.compile(
    r"\b(?:believes?|(?:is|are) convinced|thinks? that|is of the opinion"
    r"|(?:strong|firm|fixed|personal|own|has an?|holds?) opinions?|opinionated"
    r"|thinks? (?:\w+ ){1,5}(?:is|are) (?:the )?"
    r"(?:best|worst|wrong|right|better|worse|bad|good|overrated|useless|dead))\b"
)

# Health, religion, politics, sexuality, ethnicity, immigration/legal status, personal finances,
# family/relationships. Matched on the Turkish-folded lowercase line text and quote. English
# words end at a word boundary; Turkish stems take any suffix. Left out on purpose: "party"
# (third-party, batch job), "race" (race condition), "diagnos*" (debugging), "secim" alone
# (UI selection), "esim" (eSIM), "health check" (probes), "technical debt"/"teknik borc",
# "leader election" (distributed systems).
SENSITIVE_RE = re.compile(
    r"\b(?:"
    r"(?<!tech )(?<!technical )debt|(?<!teknik )borc\w*|"
    r"(?<!leader )election|(?:genel|yerel|erken) secim\w*|"
    r"health(?![- ]?check|\s*endpoint|\s*probe)|saglik(?!\s+kontrol)\w*|"
    r"illness|disease|cancer|diabet\w*|adhd|autis[mt]\w*|insomnia|anxiety|burnout|surgery|"
    r"depress(?:ion|ed)|therap(?:y|ist)|medication|hastalik\w*|hastane\w*|depresyon\w*|"
    r"terapi\w*|ilac\w*|"
    r"religio\w*|pray(?:er|ers|ed)?|church|mosque|synagogue|muslim|atheist|jewish|buddhist|"
    r"din|dini\w*|dinsel\w*|namaz\w*|musluman\w*|ateist\w*|"
    r"politic\w*|siyaset\w*|siyasi\w*|"
    r"salary|mortgage|bankrupt\w*|maas\w*|"
    r"divorce\w*|girlfriend|boyfriend|wife|husband|spouse|pregnan\w*|"
    r"bosan(?:ma|dim|di|iyor)\w*|(?:kiz|erkek) arkadas\w*|sevgilim\w*|hamile\w*|"
    r"visa|immigration|asylum|vize\w*|"
    r"sexuality|gay|lesbian|transgender|lgbt\w*|escinsel\w*|"
    r"ethnicity|ethnic|racial|etnik\w*"
    r")\b"
)


def content_problem(text: str, quote: str) -> str | None:
    if STANCE_RE.search(text.lower()):
        return "stance"
    if SENSITIVE_RE.search(fold(text, "tr")) or SENSITIVE_RE.search(fold(quote, "tr")):
        return "sensitive"
    return None
