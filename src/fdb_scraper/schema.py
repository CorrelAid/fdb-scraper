"""The published table's contract: what it contains, and what is checked.

What is *decided* -- which fields are dropped, what they are renamed to, which
taxonomies are collapsed, what a value has to look like -- is declared in
:mod:`fdb_scraper.config` and read from there. This file turns those decisions into
a checkable schema, in reading order:

* :data:`USEABLE_FIELDS` -- what the parser is asked for, per
  :data:`~fdb_scraper.config.DROPPED_FIELDS`.
* :data:`COLUMNS` -- dtype, nullability, uniqueness and value checks per column.
* :data:`PUBLISHED_FIELDS` -- the output, in order.
* :data:`ORIGIN` -- upstream, derived or inferred, per column. The one thing a
  consumer cannot read off the values themselves.

:func:`describe` renders all of that as a table, which is usually the faster way
to answer a question about the output than reading this file.

:mod:`fdb_scraper.process` applies these declarations; it holds no declarations of
its own, so the two never disagree. Import direction is process -> schema -> config.
"""

from __future__ import annotations

from collections.abc import Iterable

import pandera.polars as pa
import polars as pl

from fdb_scraper.config import (
    CONSUMED_FIELDS,
    CONTACT_PREFIX,
    DROPPED_FIELDS,
    FAR_FUTURE,
    INFERRED_COLUMNS,
    ISSUE_EPOCH,
    LICENCE_LABEL,
    LOAD_EPOCH,
    OPEN_LINK_FIELDS,
    PIVOTS,
    RENAMES,
    SEPARATOR,
    SLUG,
    TERM_EDGE,
    TEXT_FIELDS,
    URL_PREFIX,
)
from fdb_scraper.links import CONTACT_KEYS
from fdb_scraper.parser import ALL_FIELDS
from fdb_scraper.parser import INVISIBLE_RE
from fdb_scraper.generated import CLOSED_VOCABS

USEABLE_FIELDS: tuple[str, ...] = tuple(f for f in ALL_FIELDS if f not in DROPPED_FIELDS)

CONTACT_COLUMNS = tuple(f"{CONTACT_PREFIX}{f}" for f in CONTACT_KEYS)

# Source columns that no longer exist once the pivots are collapsed.
PIVOTED_SOURCES = frozenset(c for parents in PIVOTS.values() for c in parents)


def pivot_paths(target: str) -> tuple[str, ...]:
    """Every "parent.child" path the export's vocabularies allow for ``target``."""
    return tuple(
        sorted(
            f"{parent}{SEPARATOR}{code}"
            for column, parent in PIVOTS[target].items()
            for code in CLOSED_VOCABS[column]
        )
    )


# The timestamp dtype the history columns carry. Named because the aggregates that
# derive them have to be cast to it explicitly: on a first load nothing has been
# retired yet, so the aggregates come out Null and List(Null).
TIMESTAMP = pl.Datetime(time_unit="us", time_zone="UTC")


def _is_span_partition(keywords: str | None, extracted: list[str] | None) -> bool:
    """Whether ``extracted`` is exactly ``keywords`` cut into contiguous spans.

    The property that makes an inferred column publishable beside a checked
    contract: every keyword is a contiguous span of the source, in source order,
    and the spans cover the whole string. Invention, omission and reordering fail
    here rather than being merely untested -- what remains unverifiable is
    boundary *placement*, which is what the segmenter's held-out score measures.

    Tokens are compared with edge punctuation stripped, because a group's outer
    punctuation is dropped from the published term ("Sprache," -> "Sprache") and a
    group that strips to nothing is dropped entirely.
    """
    if extracted is None:
        # Not segmented: the raw string may still be there, and a null is how a
        # value the tagger has not seen is published.
        return True
    if keywords is None:
        return not extracted  # nothing to be a span of
    tokens = [t for t in keywords.split() if t.strip(TERM_EDGE)]
    produced = [
        t
        for term in extracted
        for t in term.split()
        if t.strip(TERM_EDGE)
    ]
    return [t.strip(TERM_EDGE) for t in tokens] == [
        t.strip(TERM_EDGE) for t in produced
    ]


def _keyword_spans() -> pa.Check:
    """The span invariant as a frame-level check, since it spans two columns."""

    def _check(data: pa.PolarsData) -> pl.LazyFrame:
        frame = data.lazyframe.select("keywords", "keywords_extracted").collect()
        return pl.LazyFrame(
            {
                "keyword_spans": [
                    _is_span_partition(keywords, extracted)
                    for keywords, extracted in zip(
                        frame["keywords"], frame["keywords_extracted"]
                    )
                ]
            }
        )

    return pa.Check(
        _check,
        name="keyword_spans",
        description=(
            "every extracted keyword is a contiguous span of the raw keywords "
            "string, and the spans cover it exactly"
        ),
    )


def _list_elements(check: pl.Expr, name: str, description: str) -> pa.Check:
    """Apply an element-wise expression to every item of a list column.

    Pandera has no built-in element check for nested dtypes, so evaluate the
    expression inside ``list.eval`` and require it to hold for the whole list.
    Empty lists pass, which is what we want -- absence is not a violation.
    """

    def _check(data: pa.PolarsData) -> pl.LazyFrame:
        # fill_null(False): list.all() ignores nulls, so without this a column of
        # unresolved elements would pass the check vacuously.
        return data.lazyframe.select(
            pl.col(data.key).list.eval(check.fill_null(False)).list.all()
        )

    return pa.Check(_check, name=name, description=description)


def _text_column() -> pa.Column:
    """A text column the parser has already cleaned.

    Asserts what ``parser._clean`` guarantees: no soft hyphen, no C0/C1 control
    character, no run of whitespace, no leading or trailing space. Cheap, and it
    catches a text field added to the parser without going through ``_clean`` --
    which is how ``keywords`` shipped a literal tab.
    """
    return pa.Column(
        pl.String,
        nullable=True,
        checks=pa.Check(
            lambda data: data.lazyframe.select(
                ~pl.col(data.key).str.contains(INVISIBLE_RE.pattern)
                & ~pl.col(data.key).str.contains(r"\s\s|^\s|\s$")
            ),
            name="cleaned_text",
            description="no invisible characters, no stray whitespace",
        ),
    )


def _closed_vocab(field: str, vocab_key: str) -> pa.Column:
    vocab = list(CLOSED_VOCABS[vocab_key])  # the codes; labels are for display
    return pa.Column(
        pl.List(pl.String),
        nullable=True,
        checks=_list_elements(
            pl.element().is_in(vocab),
            name=f"{field}_closed_vocab",
            description=f"one of the {len(vocab)} known {field} categories",
        ),
    )


COLUMNS: dict[str, pa.Column] = {
    # Not called "id": it does not identify a row. The same programme slug exists
    # under several funding levels (agrarinvestitionsfoerderungsprogramm runs in
    # Hessen, Mecklenburg-Vorpommern and Sachsen-Anhalt as three separate
    # programmes), so 22 slugs are shared by 48 rows -- 2474 distinct of 2500.
    # Useful as a grouping key for "the same scheme elsewhere", not as a key.
    "programme_slug": pa.Column(
        pl.String, nullable=False, checks=pa.Check.str_matches(SLUG)
    ),
    # id_url keeps the whole path, so it does separate them -- 2500 distinct. Not
    # declared unique because uniqueness is inherited from url rather than
    # guaranteed: the derivation lowercases and maps "/" to "-", which two
    # sufficiently unlucky paths could collide on.
    "id_url": pa.Column(pl.String, nullable=False, checks=pa.Check.str_matches(SLUG)),
    "id_hash": pa.Column(
        pl.String, nullable=False, unique=True, checks=pa.Check.str_matches(r"^[0-9a-f]{32}$")
    ),
    "url": pa.Column(
        pl.String, nullable=False, unique=True, checks=pa.Check.str_matches(URL_PREFIX)
    ),
    # The attribution a reuser of one row has to reproduce. Never null: a row
    # without it is a row someone cannot lawfully republish, so an empty value is a
    # bug rather than missing data. Checked for the licence name rather than matched
    # whole, so rewording the sentence does not require touching the schema.
    "license_info": pa.Column(
        pl.String,
        nullable=False,
        checks=pa.Check.str_contains(LICENCE_LABEL),
    ),
    "further_links": pa.Column(
        pl.List(pl.Struct({"url": pl.String, "title": pl.String})),
        nullable=True,
        checks=_list_elements(
            pl.element().struct.field("url").str.starts_with("http"),
            name="further_links_resolved",
            description="every link resolved to an http(s) target",
        ),
    ),
    **{f: pa.Column(pl.String, nullable=True) for f in CONTACT_COLUMNS},
    "date_of_issue": pa.Column(
        pl.Datetime(time_unit="us", time_zone="UTC"),
        nullable=True,
        checks=pa.Check.in_range(ISSUE_EPOCH, FAR_FUTURE),
    ),
    **{f: _text_column() for f in TEXT_FIELDS},
    **{
        f: pa.Column(
            pl.List(pl.String),
            nullable=True,
            checks=_list_elements(
                pl.element().str.contains(SLUG),
                name=f"{f}_leaf_label",
                description="link reduced to its leaf label",
            ),
        )
        for f in OPEN_LINK_FIELDS
    },
    # One list of "parent.child" paths per collapsed taxonomy, in place of the
    # 21 source columns the export ships them pivoted across.
    **{
        target: pa.Column(
            pl.List(pl.String),
            nullable=True,
            checks=_list_elements(
                pl.element().is_in(list(pivot_paths(target))),
                name=f"{target}_closed_vocab",
                description=f"one of the {len(pivot_paths(target))} known {target} pairs",
            ),
        )
        for target in PIVOTS
    },
    # Vocabularies are keyed by export field name; the published name may differ.
    # The pivoted ones are folded into a path column and have none of their own.
    **{
        RENAMES.get(f, f): _closed_vocab(RENAMES.get(f, f), f)
        for f in CLOSED_VOCABS
        if f not in PIVOTED_SOURCES
    },
    # --- History -------------------------------------------------------------
    # Not in the export, which only ever ships the current state. Derived from the
    # scd2 table by :func:`fdb_scraper.history.fold`, which is why they are absent
    # from a plain :func:`fdb_scraper.collect` -- see EXPORT_FIELDS.
    "on_website_from": pa.Column(
        TIMESTAMP,
        nullable=False,
        checks=pa.Check.in_range(LOAD_EPOCH, FAR_FUTURE),
    ),
    # Null until the programme changes for the first time: there is no
    # modification date for something that has only ever had one version.
    # For absent programmes, this is the most recent content change, not the
    # absence date (which is on_website_to).
    "last_updated": pa.Column(
        TIMESTAMP,
        nullable=True,
        checks=pa.Check.in_range(LOAD_EPOCH, FAR_FUTURE),
    ),
    # Empty rather than null for a programme that has never changed, so a
    # consumer can count changes without a null check. For absent programmes,
    # excludes the final on_website_to (the absence date), containing only
    # actual content changes.
    "previous_update_dates": pa.Column(
        pl.List(TIMESTAMP),
        nullable=False,
        checks=_list_elements(
            pl.element().is_between(LOAD_EPOCH, FAR_FUTURE),
            name="previous_update_dates_plausible",
            description="every recorded content change within the life of this dataset",
        ),
    ),
    # When the programme left the export. Null for programmes still present.
    # For absent programmes, this is when the programme disappeared, which may
    # be much later than the last content change (last_updated).
    "on_website_to": pa.Column(
        TIMESTAMP,
        nullable=True,
        checks=pa.Check.in_range(LOAD_EPOCH, FAR_FUTURE),
    ),
    # True once the programme has left the export. Its values are the last ones
    # published rather than nulls, so an absent programme stays readable.
    # Renamed from "deleted" because absence from the export may mean the programme
    # ended, was reorganized, or upstream restructured their data.
    "absent": pa.Column(pl.Boolean, nullable=False),
    # --- Inferred ------------------------------------------------------------
    # Nullable, and null for more than just a null ``keywords``: a value the
    # segmenter has not been run over yet publishes as null rather than as a guess.
    # The per-element check is the cheap half of the contract; the span invariant
    # that ties it to ``keywords`` is a frame-level check, added by build_schema.
    "keywords_extracted": pa.Column(
        pl.List(pl.String),
        nullable=True,
        checks=_list_elements(
            pl.element().str.len_chars() > 0,
            name="keywords_extracted_non_empty",
            description="no empty keyword",
        ),
    ),
}

# What one export run can produce on its own. This is what ``collect`` returns and
# what the parser is tested against.
EXPORT_FIELDS: tuple[str, ...] = (
    *(
        RENAMES.get(f, f)
        for f in USEABLE_FIELDS
        if f not in CONSUMED_FIELDS and f not in PIVOTED_SOURCES
    ),
    *PIVOTS,
    "id_url",
    "id_hash",
    *CONTACT_COLUMNS,
    "further_links",
    "license_info",
)

# Derived from the load history rather than from any single export.
HISTORY_COLUMNS: tuple[str, ...] = (
    "on_website_from",
    "last_updated",
    "previous_update_dates",
    "on_website_to",
    "absent",
)

# What is published. History and the inferred column last, in that order: the
# export fields keep the order they have always had, so neither addition moves an
# existing column.
PUBLISHED_FIELDS: tuple[str, ...] = (
    *EXPORT_FIELDS,
    *HISTORY_COLUMNS,
    *INFERRED_COLUMNS,
)

_missing = set(PUBLISHED_FIELDS) - set(COLUMNS)
if _missing:  # pragma: no cover -- guards against a field added to the parser
    raise RuntimeError(f"fields without a schema entry: {sorted(_missing)}")


def export_field(column: str) -> str | None:
    """The export property a published column restates, or None if it is not one."""
    return next(
        (k for k, v in RENAMES.items() if v == column),
        column if column in ALL_FIELDS else None,
    )


def origin(column: str) -> str:
    """Where a published column's values come from.

    ``upstream``
        The export states it. Reshaped on the way out -- renamed, stripped to a
        leaf label, several columns folded into one path column -- but not
        computed: every value is a value upstream supplied.
    ``derived``
        Computed here by a rule: the identifiers from the URL, the contact and
        link columns from the linked-document index, the attribution line, the
        history from the load record. Exact and reproducible, no judgement.
    ``inferred``
        Produced by a model. Not deterministic, not exact, and *measured* rather
        than guaranteed -- see services/keyword_segmenter/README.md for the
        held-out score and what the span invariant does and does not cover.

    The distinction is published, not internal: the CSV shows a column of German
    keywords with nothing to say that one of them was worked out by a classifier.
    """
    if column in INFERRED_COLUMNS:
        return "inferred"
    if column in PIVOTS or export_field(column) is not None:
        return "upstream"
    return "derived"


ORIGIN: dict[str, str] = {f: origin(f) for f in PUBLISHED_FIELDS}


def describe() -> pl.DataFrame:
    """The published contract as a table: one row per column, in output order.

    So that "what does the result look like, and what is checked" can be answered
    by looking rather than by reading this file:

        >>> import polars as pl
        >>> from fdb_scraper import describe
        >>> with pl.Config(tbl_rows=-1, fmt_str_lengths=60):
        ...     print(describe())
    """
    rows = []
    for name in PUBLISHED_FIELDS:
        col = COLUMNS[name]
        checks = col.checks if isinstance(col.checks, list) else [col.checks]
        rows.append(
            {
                "column": name,
                "dtype": str(col.dtype),
                "required": not col.nullable,
                "unique": bool(col.unique),
                "origin": ORIGIN[name],
                "export_field": export_field(name),
                "checks": ", ".join(
                    c.name or c.description or "?" for c in checks if c is not None
                )
                or None,
            }
        )
    return pl.DataFrame(
        rows,
        schema={
            "column": pl.String,
            "dtype": pl.String,
            "required": pl.Boolean,
            "unique": pl.Boolean,
            "origin": pl.String,
            "export_field": pl.String,
            "checks": pl.String,
        },
    )


def build_schema(fields: Iterable[str]) -> pa.DataFrameSchema:
    """Schema for exactly ``fields``, so a requested subset can be validated too.

    The frame-level checks are added only when the columns they relate are both
    present, so validating a subset stays possible.
    """
    fields = list(fields)
    checks = (
        [_keyword_spans()]
        if {"keywords", "keywords_extracted"} <= set(fields)
        else []
    )
    return pa.DataFrameSchema(
        {f: COLUMNS[f] for f in fields},
        strict=True,
        checks=checks,
        name="foerderdatenbank_programmes",
    )
