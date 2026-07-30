"""The published table's contract: what it contains, and what is checked.

One file, so that "what does the result look like" needs no other. In reading
order:

* :data:`DROPPED_FIELDS` -- what the parser is not asked for, and why.
* :data:`CODE_ALIASES` -- upstream category records that are duplicates, collapsed.
* :data:`RENAMES` -- export field to published name. The XML property names come
  from a generic CMS template and several mean the opposite of what they say.
* :data:`CONSUMED_FIELDS` -- parsed because a later step needs it, then dropped.
* :data:`PIVOTS` -- the two taxonomies the export ships spread across one column
  per parent value, republished as one column of ``"parent.child"`` paths each.
* :data:`COLUMNS` -- dtype, nullability, uniqueness and value checks per column.
* :data:`PUBLISHED_FIELDS` -- the output, in order.
* :data:`ORIGIN` -- upstream, derived or inferred, per column. The one thing a
  consumer cannot read off the values themselves.

:func:`describe` renders all of that as a table, which is usually the faster way
to answer a question about the output than reading this file.

:mod:`fdb_scraper.process` applies these declarations; it holds no declarations of
its own, so the two never disagree. Import direction is process -> schema.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

import pandera.polars as pa
import polars as pl

from fdb_scraper.links import CONTACT_KEYS
from fdb_scraper.scraper import ALL_FIELDS
from fdb_scraper.scraper import INVISIBLE_RE
from fdb_scraper.generated import CLOSED_VOCABS

# Fields carrying no usable information in the export. Kept out of the default
# field list rather than deleted from the parser, so a field that starts being
# populated upstream can be picked up again by asking for it explicitly.
DROPPED_FIELDS = frozenset(
    {
        # Temporary extraction directory -- meaningless once the run ends.
        "path",
        # Never populated: Null dtype across every programme.
        "challenge",
        "customer_benefit",
        "proc_quality",
        "requirements",
        "service_description",
        "service_fee_descr",
        "terms_of_payment",
        # Always an empty link list.
        "foerdertermin",
        # Single value ("Deutsch") on all but three programmes.
        "languages",
        # Mostly nonsense: of 638 non-null values, 480 fall outside any
        # plausible range (the minimum is year 0207).
        "date_of_expiration",
        # Mix of year labels ("01".."20") and buckets ("nicht_relevant"),
        # dominated by "nicht_relevant"; not usable as stated.
        "unternehmensalter",
        # Internal editorial ticket ids ("# 861223"), non-null on 2495 of 2500.
        "comment",
        # Opaque 14-digit numbers ("99102158080000") on 10 programmes, with
        # nothing upstream that says what registry they belong to or how to
        # resolve them.
        "external_id",
        # The CMS's robots noindex flag: 0 on 2023 programmes, absent on 477,
        # never 1. The 0-vs-absent split records whether the CMS wrote the
        # property, not anything about the programme -- the two groups are
        # indistinguishable on every other column. Notebook E9 watches `raw` for
        # upstream starting to set it, which would plausibly mean a withdrawal.
        "should_not_be_indexed",
        # Constant: "ServiceOffer-FundingProgram" on 2497 programmes, absent on 3.
        # Those 3 are exactly the rows where `title` is null (E12), so the only
        # usable thing about the column duplicates a check consumers are already
        # told to make.
        "subtype",
        # gsb:referenceCustomer holds the funding ministry as a display string
        # ("Bundesministerium für Wirtschaft und Klimaschutz (BMWK)") on 25
        # programmes -- and every one of those rows already carries the same body
        # in foerderorganisation as a code. A label for something the table
        # states elsewhere, on 1% of rows.
        "reference_customer",
    }
)

USEABLE_FIELDS: tuple[str, ...] = tuple(f for f in ALL_FIELDS if f not in DROPPED_FIELDS)

# Upstream ships two category records for nationwide scope, "_bundesweit" (577
# programmes) and "bundesweit" (85). Their XML is byte-identical apart from the
# name, and one programme carries both, so they are a duplicate rather than two
# concepts. "_bundesweit" is canonical: it is the one with a label entry in the
# export and the one the website's filter sidebar offers, the underscore sorting
# it above the Bundesländer. Kept as an explicit alias so the collapse is
# visible and reversible; every other upstream oddity is left verbatim.
CODE_ALIASES: dict[str, dict[str, str]] = {"foerdergebiet": {"bundesweit": "_bundesweit"}}

# Export field -> published name. Page section noted where the name is not
# self-explanatory.
RENAMES = {
    # Kurzzusammenfassung > Kurztext / Volltext
    "teaser": "short_description",
    "summary": "description",
    # Rechtsgrundlage > Richtlinie, plus its citation line
    "body_text": "legal_basis",
    "proc_description": "legal_citation",
    # Zusatzinfos, one column per sub-section
    "regulatory_framework": "legal_requirements",
    "proc_method": "procedure",
    "proc_influence": "deadlines",
    "progress": "processing_time",
    "competence_descr": "required_documents",
    # Categories, keeping the names the previous dataset published
    "foerderart": "funding_type",
    "foerderbereich": "funding_area",
    "foerdergebiet": "funding_location",
    "foerderberechtigte": "eligible_applicants",
    "foerdergeber": "funding_body",
    "foerderorganisation": "funding_organisation",
    # Editorial status note, e.g. "Programm aktiv, Antragstellung nicht möglich"
    "header": "status_note",
    # gsb:functions holds the language the application has to be written in --
    # "Deutsch", "Die Antragssprache für Skizzen ist in der Regel Englisch". 11
    # distinct values on 25 programmes. It is also where the usable half of the
    # language information ended up: the languages classifier was dropped for
    # saying only "Deutsch", while this states the exceptions.
    "functions": "application_language",
    # gsb:remark holds the page's search-engine description -- second person,
    # call to action ("Beantragen Sie als Konsortium Förderung für ..."). Written
    # for a search result, not an editorial note about the programme.
    "remark": "seo_description",
    "kontakt": "contact_ids",
}

# Fields the pipeline needs as input but does not publish. Distinct from
# process.DROPPED_FIELDS, which is never parsed at all: these have to survive the
# scrape because a later step consumes them.
#
# externer_link holds "target:/BMWI/..." document references. add_links resolves
# them against the linked-document index and publishes the result as
# further_links, so the raw references are an intermediate, not an output.
CONSUMED_FIELDS = frozenset({"externer_link"})

CONTACT_PREFIX = "contact_info_"
CONTACT_COLUMNS = tuple(f"{CONTACT_PREFIX}{f}" for f in CONTACT_KEYS)
# grw and unternehmensgroesse keep their export names: GRW is the name of a law,
# and "Unternehmensgröße" buckets are defined by it rather than translatable.

# --- Columns the export ships pivoted ----------------------------------------
# Two taxonomies arrive spread across one column per parent value, because the
# export models each parent as its own classifier. Both are republished as a
# single column of "parent.child" paths.
#
# The parent has to travel in the value, in both cases for the same reason: the
# child vocabulary is shared between parents, so a bare child code cannot be
# attributed back.
#
# funding_subarea (19 -> 1)
#     The second Förderbereich level. 91% of programmes carry one, but no single
#     source column exceeds 18% fill, so as 19 columns it was 28% of the table's
#     width and 43597 empty cells in a CSV. 11 sub-area codes occur under more
#     than one parent -- "beratung_schulung" under five -- and 56% of programmes
#     list several Förderbereiche, so dropping the parent would collapse 349 of
#     5963 values.
#
# applicant_sector (2 -> 1)
#     The applicant's economic sector. The two source columns carry the
#     byte-identical eight-sector list and differ only in which applicant type it
#     describes, so their names encode a value of another vocabulary
#     (foerderberechtigte) exactly as the uf_* names encode a Förderbereich. The
#     distinction is real data, not redundancy: "Niederlassung von Ärztinnen und
#     Ärzten" is freie_berufe for a founder and dienstleistungen for an existing
#     business, and InvestEU lists all eight sectors for founders against one for
#     companies. 15 of the 206 programmes that fill both differ that way.
SEPARATOR = "."  # schema.SLUG forbids "/", and no category code contains a dot

PIVOTS: dict[str, dict[str, str]] = {
    "funding_subarea": {
        "uf_arbeit": "arbeit",
        "uf_aus_weiterbildung": "aus_weiterbildung",
        "uf_aussenwirtschaft": "aussenwirtschaft",
        "uf_beratung": "beratung",
        "uf_energieeffizienz": "energieeffizienz_erneuerbare_energien",
        "uf_existenzgruendung": "existenzgruendung_festigung",
        # The two Forschung classifier names are abbreviated upstream.
        "uf_forschung_offen": "forschung_innovation_themenoffen",
        "uf_forschung_spezifisch": "forschung_innovation_themenspezifisch",
        "uf_frauenfoerderung": "frauenfoerderung",
        "uf_gesundheit_soziales": "gesundheit_soziales",
        "uf_infrastruktur": "infrastruktur",
        "uf_kultur_medien_sport": "kultur_medien_sport",
        "uf_landwirtschaft": "landwirtschaft_laendliche_entwicklung",
        "uf_messen_ausstellungen": "messen_ausstellungen",
        "uf_regionalfoerderung": "regionalfoerderung",
        "uf_staedtebau_stadterneuerung": "staedtebau_stadterneuerung",
        "uf_umwelt_naturschutz": "umwelt_naturschutz",
        "uf_unternehmensfinanzierung": "unternehmensfinanzierung",
        "uf_wohnungsbau": "wohnungsbau_modernisierung",
    },
    "applicant_sector": {
        "branchen_existenzgruenderin": "existenzgruenderin",
        "branchen_unternehmen": "unternehmen",
    },
}

# Target column -> the vocabulary its parents come from, so a consumer can tell
# what the left half of a path is.
PIVOT_PARENT_VOCAB = {
    "funding_subarea": "foerderbereich",
    "applicant_sector": "foerderberechtigte",
}

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


# A leaf label, e.g. "keine_grw_foerderung" or "gesellschaft fuer ... (giz)-31032".
# Deliberately loose: the point is that the "target:/BMWI/..." prefix is gone,
# and upstream slugs contain mixed case, spaces, parentheses and umlauts.
SLUG = r"^[^/]+$"
URL_PREFIX = r"^https://www\.foerderdatenbank\.de/FDB/Content/DE/Foerderprogramm/"

# Open-ended link lists: new entries appear constantly, so validate the shape
# of the leaf rather than its membership.
OPEN_LINK_FIELDS = ("funding_organisation", "contact_ids")

# --- The source's licence ----------------------------------------------------
# From the Förderdatenbank imprint, which licenses the site's texts under CC BY-ND
# and names the ministry as rights holder. Declared here rather than in the
# generator so the per-row attribution and the DCAT metadata cannot disagree about
# what this data is licensed as.
#
# ND is why nothing here rewrites a value: the published table reformats the export
# into columns and resolves its internal links, which is not a derivative work of
# the texts, but editing them would be.
#
# funding_crawler recorded CC BY-ND 3.0 DE and "Wirtschaft und Klimaschutz". Both
# are now out of date -- the imprint says 4.0 and the ministry was renamed -- so
# these are taken from the current imprint rather than carried over.
SOURCE_LICENSOR = "Bundesministerium für Wirtschaft und Energie"
SOURCE_LICENCE = "CC BY-ND 4.0"
# The deed, not the dcat-ap.de URI: this one is for a human reading a cell.
SOURCE_LICENCE_URL = "https://creativecommons.org/licenses/by-nd/4.0/deed.de"

# Bounds for the history timestamps, which are load times rather than anything the
# export states. Nothing can predate the first load, and a value in the far future
# means a clock or a timezone went wrong. Deliberately not "not after now": that
# would make the check depend on when it runs.
LOAD_EPOCH = datetime(2025, 1, 1, tzinfo=timezone.utc)
FAR_FUTURE = datetime(2100, 1, 1, tzinfo=timezone.utc)

# The timestamp dtype the history columns carry. Named because the aggregates that
# derive them have to be cast to it explicitly: on a first load nothing has been
# retired yet, so the aggregates come out Null and List(Null).
TIMESTAMP = pl.Datetime(time_unit="us", time_zone="UTC")

TEXT_FIELDS = (
    "title", "description", "short_description", "legal_basis", "legal_citation",
    "legal_requirements", "procedure", "deadlines", "processing_time",
    "required_documents", "status_note", "seo_description",
    "application_language", "keywords",
)

# --- The inferred column ------------------------------------------------------
# ``keywords`` arrives as one string holding several keywords with no reliable
# separator -- 87.7% of values carry no separator signal at all, and a multi-word
# keyword is indistinguishable from several one-word ones without reading the
# German. ``keywords_extracted`` is that string split up by a fine-tuned encoder
# (services/keyword_segmenter). The raw column stays untouched beside it.
#
# It is the only published column no rule produces, which is why :data:`ORIGIN`
# exists: everything else here either restates the export or follows from it, and a
# consumer reading the CSV has no way to tell the difference by looking.
INFERRED_COLUMNS: tuple[str, ...] = ("keywords_extracted",)

# Punctuation the segmenter strips from the edges of a term. Stated here rather
# than imported from ``segment.tokens``: services/keyword_segmenter is not an
# installed package, and this is the contract the published column is held to
# whatever produced it.
_TERM_EDGE = " ,;.:()[]\"'"


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
    tokens = [t for t in keywords.split() if t.strip(_TERM_EDGE)]
    produced = [
        t
        for term in extracted
        for t in term.split()
        if t.strip(_TERM_EDGE)
    ]
    return [t.strip(_TERM_EDGE) for t in tokens] == [
        t.strip(_TERM_EDGE) for t in produced
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
    """A text column the scraper has already cleaned.

    Asserts what ``scraper._clean`` guarantees: no soft hyphen, no C0/C1 control
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
        checks=pa.Check.str_contains(SOURCE_LICENCE),
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
        checks=pa.Check.in_range(
            datetime(2000, 1, 1, tzinfo=timezone.utc),
            datetime(2100, 1, 1, tzinfo=timezone.utc),
        ),
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
    "last_updated": pa.Column(
        TIMESTAMP,
        nullable=True,
        checks=pa.Check.in_range(LOAD_EPOCH, FAR_FUTURE),
    ),
    # Empty rather than null for a programme that has never changed, so a
    # consumer can count changes without a null check.
    "previous_update_dates": pa.Column(
        pl.List(TIMESTAMP),
        nullable=False,
        checks=_list_elements(
            pl.element().is_between(LOAD_EPOCH, FAR_FUTURE),
            name="previous_update_dates_plausible",
            description="every recorded change within the life of this dataset",
        ),
    ),
    # True once the programme has left the export. Its values are the last ones
    # published rather than nulls, so a withdrawn programme stays readable.
    "deleted": pa.Column(pl.Boolean, nullable=False),
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
    "deleted",
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
