"""Deterministic repairs applied to the model's per-gap join decisions.

The tagger is trained on 120 rows whose gold terms are 93% single-token: 38 terms
of length 2, ten of length 3, two of length 4, one of length 5. Long spans are not
something it got wrong so much as something it never saw, and the probabilities
say so -- on the seven-token agency name in ``foerderung-des-deutschen-films`` the
two-token compounds score 0.93-0.98 while the internal gaps of the name sit at
0.354 and 0.460, i.e. undecided rather than confident.

Two rules cover that gap without another labelling round, and neither can invent
text: both only ever *add* joins to an already valid partition, so every published
term stays a contiguous span of the source.

``glue_function_words``
    A German keyword does not begin or end with an article, preposition or
    conjunction. "Beauftragte der" and "Bundesregierung für" are ill-formed on
    their face, and no model is needed to know it. Forcing a function word to bind
    on both sides repairs exactly the boundaries the model is unsure about.

``apply_lexicon``
    Long spans that *are* real terms are usually named entities, and the export
    already carries a list of them in ``contact_info_institution``. A run of three
    or more tokens matching an institution name is joined outright.

Order matters: the lexicon runs first so its spans are decided on the model's own
output, then function words glue whatever is left.

An explicit delimiter is never crossed. A token ending in a comma or semicolon is
the one separator the author gave us (see ``build_labels`` rule 4); overriding it
with a guess would trade evidence for inference.
"""

from __future__ import annotations

import json
from pathlib import Path

from segment.tokens import _EDGE

DEFAULT_LEXICON = Path(__file__).with_name("lexicon.json")

# Articles, the two conjunctions that appear in names, and the prepositions found
# in institution names in this corpus. Deliberately closed and small: every entry
# is a word that cannot stand as a keyword on its own, which is what licenses the
# rule. Words with a content reading in isolation ("Land", "Recht") are excluded
# even where they can be function-like.
FUNCTION_WORDS = frozenset(
    """
    der die das den dem des
    ein eine einer eines einem einen
    und oder sowie
    für in im am an auf aus bei mit nach von vom vor zu zum zur
    über unter durch gegen ohne um zwischen
    and of the for
    """.split()
)

# A delimiter the author actually wrote, as opposed to one inferred.
_DELIMITERS = ",;"

# Minimum length for a lexicon match. Two-token compounds are the model's
# strongest class (0.91-0.98 on gold pairs); overriding it there would risk
# precision for no gain, so the lexicon only speaks where the model is weak.
MIN_LEXICON_TOKENS = 3


def _bare(token: str) -> str:
    return token.strip(_EDGE).lower()


def _ends_a_field(token: str) -> bool:
    """Whether the author's own punctuation closes a keyword after this token."""
    return token.rstrip(" \"')]").endswith(tuple(_DELIMITERS))


def glue_function_words(tokens: list[str], joins: list[bool]) -> list[bool]:
    """Bind every function word to both neighbours; it can neither open nor close a term."""
    out = list(joins)
    for i, token in enumerate(tokens):
        if _bare(token) not in FUNCTION_WORDS:
            continue
        # Gap i-1 sits before the word, gap i after it. A gap the author closed
        # with punctuation stays closed.
        if i > 0 and not _ends_a_field(tokens[i - 1]):
            out[i - 1] = True
        if i < len(out) and not _ends_a_field(token):
            out[i] = True
    return out


def _lexicon_index(entries: list[list[str]]) -> dict[str, list[tuple[str, ...]]]:
    """First token (lowercased) -> entries starting with it, longest first."""
    index: dict[str, list[tuple[str, ...]]] = {}
    for entry in entries:
        key = tuple(w.lower() for w in entry)
        if len(key) >= MIN_LEXICON_TOKENS:
            index.setdefault(key[0], []).append(key)
    for key in index:
        index[key].sort(key=len, reverse=True)
    return index


def apply_lexicon(
    tokens: list[str], joins: list[bool], index: dict[str, list[tuple[str, ...]]]
) -> list[bool]:
    """Join any run of tokens that reproduces a known institution name.

    Longest match wins and matches do not overlap, so a name cannot be joined to
    its neighbour by two matches meeting at a token.
    """
    out = list(joins)
    low = [_bare(t) for t in tokens]
    i = 0
    while i < len(tokens):
        for candidate in index.get(low[i], ()):
            end = i + len(candidate)
            if end > len(tokens) or tuple(low[i:end]) != candidate:
                continue
            # A delimiter inside the span means the author split it there; that is
            # evidence about *this* row and outranks the lexicon.
            if any(_ends_a_field(tokens[j]) for j in range(i, end - 1)):
                continue
            for gap in range(i, end - 1):
                out[gap] = True
            i = end - 1
            break
        i += 1
    return out


def load_lexicon(path: str | Path | None = DEFAULT_LEXICON) -> dict[str, list[tuple[str, ...]]]:
    """Load the built lexicon, or an empty index if it was never built."""
    if path is None:
        return {}
    path = Path(path)
    if not path.exists():
        return {}
    return _lexicon_index(json.loads(path.read_text())["entries"])


def postprocess(
    tokens: list[str], joins: list[bool], index: dict[str, list[tuple[str, ...]]] | None = None
) -> list[bool]:
    """Both rules, in the order they are meant to run."""
    if index:
        joins = apply_lexicon(tokens, joins, index)
    return glue_function_words(tokens, joins)
