DOTLESS_I = "\N{LATIN SMALL LETTER DOTLESS I}"
STEM_MIN = 3
PREFIX_MIN = 4
STEM_MAX_CANDIDATES = 24
MAX_SUFFIXES = 2
NORM = str.maketrans("ıüöşçğ", "iuoscg")
VOWELS = "aeiou"
NO_CLUSTER_END = "aeioubcdgkp"
SOFTEN_BACK = {"b": "p", "d": "t", "g": "k"}
SUFFIXES = (
    "nin", "nun", "den", "dan", "ten", "tan",
    "ler", "lar", "si", "su", "de", "da", "te", "ta",
    "ye", "ya", "yi", "yu", "ni", "nu", "ne", "na", "in", "un",
    "e", "a", "i", "u",
)  # fmt: skip


def norm(word: str) -> str:
    return word.translate(NORM)


def strip_chain(word: str) -> list[tuple[str, str]]:
    """(stem, innermost suffix) pairs for up to MAX_SUFFIXES stripped suffixes."""
    shape = norm(word)
    out: list[tuple[str, str]] = []
    layer = [(len(word), "")]
    for _ in range(MAX_SUFFIXES):
        nxt: list[tuple[int, str]] = []
        for end, _last in layer:
            for suf in SUFFIXES:
                cut = end - len(suf)
                if cut >= STEM_MIN and shape[:end].endswith(suf):
                    nxt.append((cut, suf))
        out += [(word[:cut], suf) for cut, suf in nxt]
        layer = nxt
    return out


def variants(stem: str, suffix: str, preset: str) -> list[str]:
    found = [stem]
    if suffix[0] in VOWELS:
        if stem[-1] in SOFTEN_BACK:
            found.append(stem[:-1] + SOFTEN_BACK[stem[-1]])
        shape = norm(stem)
        if (
            len(shape) >= 3
            and shape[-1] not in NO_CLUSTER_END
            and shape[-2] not in VOWELS
            and sum(c in VOWELS for c in shape) == 1
        ):
            inserts = ("i", "u") if preset == "tr" else ("i", DOTLESS_I, "u")
            found += [stem[:-1] + v + stem[-1] for v in inserts]
    return found


def stem_term(stem: str) -> str:
    return f'"{stem}"*' if len(stem) >= PREFIX_MIN else f'"{stem}"'


def candidates(word: str, preset: str = "none") -> list[str]:
    """Candidate Turkish stems of an inflected word (lowercase, already folded to the index form).

    Suffixes are stripped (at most two), consonant softening and vowel drop are
    undone only before a vowel-initial suffix, and a stem shorter than STEM_MIN
    is dropped, so "evde" yields nothing. Stems of PREFIX_MIN or more letters
    match as FTS5 prefixes (see stem_term), three-letter stems only as whole words.
    """
    if not word.isalpha():
        return []
    out: dict[str, None] = {}
    for stem, suffix in strip_chain(word):
        for cand in variants(stem, suffix, preset):
            if len(cand) >= STEM_MIN and cand != word:
                out[cand] = None
    return list(out)[:STEM_MAX_CANDIDATES]
