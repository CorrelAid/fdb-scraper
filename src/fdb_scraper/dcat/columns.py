"""What is said about one published column, in the two places a consumer looks.

The CSVW table schema and the minted vocabulary describe the same columns from
different angles -- a cell to parse versus a property to dereference -- so what is
said about a column is decided here once and both builders read it. A note that
existed twice would drift.

Two distinctions this module keeps straight:

*datatype versus range*
    :func:`datatype_of` types the *cell* a validator parses; :func:`range_of`
    types one *value* of the property. They differ exactly on the list columns,
    whose cell is one JSON array and whose values are its members.

*generated versus written*
    Multiplicity, patterns and vocabulary sizes are read off the pandera schema,
    so they cannot contradict what the pipeline enforces. Meaning and inference
    notes are hand-written and therefore declared in
    :mod:`fdb_scraper.config`; this module only decides which of them apply to which
    column.
"""

from __future__ import annotations

import polars as pl
from rdflib import URIRef
from rdflib.namespace import XSD

from fdb_scraper.config import (
    INFERRED_NOTES,
    MEANING_NOTES,
    PIVOT_PARENT_VOCAB,
    PIVOTS,
    SEPARATOR,
)
from fdb_scraper.generated import CLOSED_VOCABS
from fdb_scraper.schema import COLUMNS, PUBLISHED_FIELDS, pivot_paths
from fdb_scraper.semantics import SCHEME_VOCAB, SCHEMES


def polars_dtype(column: str):
    """The polars dtype behind a pandera column."""
    col = COLUMNS[column]
    return col.dtype.type if hasattr(col.dtype, "type") else col.dtype


def _xsd(dtype) -> URIRef | None:
    """XSD datatype for a polars dtype, or None where none applies."""
    if isinstance(dtype, pl.Struct):
        return None
    if dtype == pl.Boolean:
        return XSD.boolean
    if dtype == pl.Int64:
        return XSD.integer
    if isinstance(dtype, pl.Datetime):
        return XSD.dateTime
    return XSD.string


def range_of(column: str) -> URIRef | None:
    """``rdfs:range`` for a minted term, from the column's dtype.

    A list column ranges over its member type: the range of a property is what
    each of its values is, and each value of a list column is one element.
    ``further_links`` is a list of structs, which no XSD datatype describes, so it
    gets no range rather than a wrong one -- its encoding is stated in the CSVW
    schema instead.
    """
    dtype = polars_dtype(column)
    if isinstance(dtype, pl.List):
        dtype = dtype.inner
    return _xsd(dtype)


_unknown_notes = sorted(
    (set(MEANING_NOTES) | set(INFERRED_NOTES)) - set(PUBLISHED_FIELDS)
)
if _unknown_notes:  # pragma: no cover -- guards a column renamed out from under a note
    raise RuntimeError(f"notes for columns that are not published: {_unknown_notes}")


def inferred_note(column: str) -> str | None:
    """What produced an inferred column and how well, or None if a rule produced it."""
    return INFERRED_NOTES.get(column)


def meaning_note(column: str) -> str | None:
    """What a column's values mean, where the name does not say it."""
    return MEANING_NOTES.get(column)


def vocab_note(column: str) -> str | None:
    """How a column's closed vocabulary constrains it, or None if it has none.

    Shared by the CSVW schema and the minted vocabulary so one description of a
    column cannot drift from the other.
    """
    if (scheme := SCHEMES.get(column)) is None:
        return None
    if column in PIVOTS:
        parent = PIVOT_PARENT_VOCAB[column]
        return (
            f"closed vocabulary, {len(pivot_paths(column))} "
            f'"parent{SEPARATOR}child" paths, where parent is a {parent} code; '
            "one value per pair"
        )
    # SCHEMES gives the scheme name; two columns share one, so the vocabulary
    # the codes come from has to be looked up.
    source = SCHEME_VOCAB[scheme]
    return (
        f"closed vocabulary, {len(CLOSED_VOCABS[source])} codes; labels in "
        f"fdb_scraper.generated.vocab.{source.upper()}"
    )


def datatype_of(column: str) -> dict | str | None:
    """CSVW ``datatype`` for a published column, carrying over the schema's checks.

    Pandera's checks store their arguments, so ``str_matches`` becomes a CSVW
    ``format`` regex and ``in_range`` becomes ``minimum``/``maximum``. What CSVW
    cannot express is the closed vocabularies -- it has no enumeration -- so
    those are named in the column description instead, by :func:`vocab_note`.

    A list column is ``string``, unlike :func:`range_of`, which types the member.
    ``datatype`` in CSVW is what a validator parses the cell against, and the cell
    of a list column is one JSON array, not one member -- typing it by the member
    would make every such cell invalid (``[]`` is no ``dateTime``). What the
    members are is stated by :func:`list_note` instead, and the member type is
    still declared where it applies: ``rdfs:range`` on the minted term.
    """
    col = COLUMNS[column]
    dtype = polars_dtype(column)
    if isinstance(dtype, pl.List):
        return "string"
    if isinstance(dtype, pl.Struct):
        return None
    checks = {c.name: c._check_kwargs for c in col.checks}

    if dtype == pl.Boolean:
        return "boolean"
    if dtype == pl.Int64:
        return "integer"
    if isinstance(dtype, pl.Datetime):
        out: dict = {"base": "dateTime"}
        if (rng := checks.get("in_range")) is not None:
            out["minimum"] = rng["min_value"].isoformat()
            out["maximum"] = rng["max_value"].isoformat()
        return out
    if (pattern := checks.get("str_matches", {}).get("pattern")) is not None:
        return {"base": "string", "format": pattern}
    return "string"


def list_note(column: str) -> str | None:
    """What a list column's cell holds, or None if the column holds one value.

    Stated in prose because the schema no longer describes a flat file: without a
    separator convention there is no CSVW construct for "many values per row", and
    inventing one would describe an encoding nobody produces. So the cell is typed
    ``string`` by :func:`datatype_of` and its contents said here -- the member type
    included, since that is the one thing the ``datatype`` cannot carry.
    """
    dtype = polars_dtype(column)
    if not isinstance(dtype, pl.List):
        return None
    if isinstance(dtype.inner, pl.Struct):
        fields = ", ".join(f.name for f in dtype.inner.fields)
        members = f"objects, each with {fields}"
    elif isinstance(dtype.inner, pl.Datetime):
        members = "xsd:dateTime strings"
    else:
        members = "strings"
    return f"zero or more values per row, JSON-encoded in the cell as an array of {members}"


def description_of(column: str) -> str | None:
    """Everything worth saying about a column, or None if its name says it all.

    One string, because CSVW takes one ``dc:description`` per column. The order is
    fixed -- what the values mean, how many there are per cell, what constrains
    them, how they were produced -- so two columns never read as two different
    kinds of documentation. The vocabulary states the same notes as separate
    ``rdfs:comment`` triples instead, which RDF allows and a reader benefits from.
    """
    notes = [
        note
        for note in (
            meaning_note(column),
            list_note(column),
            vocab_note(column),
            inferred_note(column),
        )
        if note is not None
    ]
    return "; ".join(notes) if notes else None
