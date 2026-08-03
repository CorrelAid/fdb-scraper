"""One deterministic repair applied to the model's per-gap join decisions.

This exists because of a gap in the *first* labelling round, whose 70 training rows
were 93% single-token terms with a longest span of five. Long spans were not
something the model got wrong so much as something it had never seen, and its
probabilities said so: on the seven-token agency name in
``foerderung-des-deutschen-films`` the two-token compounds scored 0.93-0.98 while
the name's own internal gaps sat at 0.354 and 0.460 -- undecided, not confident.

The second labelling round taught the model those spans directly, and this rule's
footprint fell from 105 changed values to 31. It is kept as a guard rather than a
fix: the boundaries it repairs are ill-formed German whatever the checkpoint says,
so a future retrain cannot reintroduce them.

One rule covers that gap, and it cannot invent text: it only ever *adds* joins to an
already valid partition, so every published term stays a contiguous span of the
source. A German keyword does not begin or end with an article, preposition or
conjunction -- "Beauftragte der" and "Bundesregierung für" are ill-formed on their
face, and no model is needed to know it. Forcing a function word to bind on both
sides repairs exactly the boundaries the model is unsure about.

A second rule was removed rather than kept: a gazetteer of institution names from
``contact_info_institution``, which was worth three corrected rows against the
70-label checkpoint and **zero** against the 131-label one. Once the model had seen
long spans it needed no help finding them, and a 646-entry file that changes nothing
is not a safety net, it is a thing to maintain. ``git log`` has it if the case for it
ever returns.

An explicit delimiter is never crossed. A token ending in a comma or semicolon is
the one separator the author gave us (see ``build_labels`` rule 4); overriding it
with a guess would trade evidence for inference.
"""

from __future__ import annotations

from segment.tokens import _EDGE

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

