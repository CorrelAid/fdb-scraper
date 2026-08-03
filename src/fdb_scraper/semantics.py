"""Column -> RDF predicate, for describing the published table to consumers.

:mod:`fdb_scraper.schema` says what a column *contains* (dtype, pattern, closed
vocabulary). It cannot say what a column *means*: ``id``, ``id_url`` and
``id_hash`` are all non-null strings matching a slug pattern, and they identify
three different things. That judgement is :data:`fdb_scraper.config.EXTERNAL`,
made by hand; this module applies it.

The rule it applies is reuse versus invention:

* a column in :data:`~fdb_scraper.config.EXTERNAL` -- an established term genuinely
  describes it, so point at that term. Consumers already understand ``dct:title``
  and ``vcard:locality``.
* everything else -- no established term fits, so mint one under
  :data:`fdb_scraper.config.VOCAB` rather than bend a foreign term out of shape.
  Same rule as :mod:`fdb_scraper.codelists`, where a category with no honest
  counterpart is left unmapped instead of folded into a near-miss.

A new published column therefore needs no edit to be describable -- it gets a
minted term -- but it will not gain a reused one by accident either. Import raises
if ``EXTERNAL`` names a column that is no longer published, or if a mapping uses a
prefix ``NAMESPACES`` does not declare, so neither can be discovered in a published
document.
"""

from __future__ import annotations

from fdb_scraper.config import (
    EXTERNAL,
    NAMESPACES,
    ORIGIN_TERM,
    PIVOTS,
    RENAMES,
)
from fdb_scraper.generated import CLOSED_VOCABS
from fdb_scraper.schema import PIVOTED_SOURCES, PUBLISHED_FIELDS

# Published column -> the concept scheme its values come from. Derived rather than
# typed, so a new closed vocabulary is picked up without an edit here. The pivoted
# vocabularies are excluded: process.collapse_pivots folds each group into one
# column of "parent.child" paths, which needs a scheme of its own.
SCHEMES = {
    **{RENAMES.get(f, f): f for f in CLOSED_VOCABS if f not in PIVOTED_SOURCES},
    **{target: target for target in PIVOTS},
}

# Scheme name -> the vocabulary its child codes come from, for the generator. A
# pivot's children all share one vocabulary, so any source column will do.
SCHEME_VOCAB = {
    **{f: f for f in CLOSED_VOCABS if f not in PIVOTED_SOURCES},
    **{target: next(iter(parents)) for target, parents in PIVOTS.items()},
}

# Minted terms that describe a *column* rather than hold a value of one, so they are
# not in PREDICATES and no published column maps to them. Named here so the
# generator and the "every minted term is defined" test agree on which terms the
# vocabulary document is expected to carry beyond the column predicates.
ANNOTATIONS = (ORIGIN_TERM,)


def _camel(column: str) -> str:
    head, *rest = column.split("_")
    return head + "".join(part.title() for part in rest)


def predicate(column: str) -> str:
    """CURIE for ``column``, reusing a foreign term where one honestly fits."""
    return EXTERNAL.get(column, f"fdb:{_camel(column)}")


def expand(curie: str) -> str:
    prefix, _, local = curie.partition(":")
    return NAMESPACES[prefix] + local


PREDICATES = {f: predicate(f) for f in PUBLISHED_FIELDS}

_unknown_prefix = sorted(
    c for c in PREDICATES.values() if c.partition(":")[0] not in NAMESPACES
)
if _unknown_prefix:  # pragma: no cover -- guards a typo in EXTERNAL
    raise RuntimeError(f"predicates with an undeclared prefix: {_unknown_prefix}")

_stale = sorted(set(EXTERNAL) - set(PUBLISHED_FIELDS))
if _stale:  # pragma: no cover -- guards a column renamed out from under EXTERNAL
    raise RuntimeError(f"EXTERNAL maps columns that are not published: {_stale}")
