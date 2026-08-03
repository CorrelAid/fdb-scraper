import pandera.errors
import polars as pl
import pytest

from fdb_scraper import collect
from fdb_scraper.config import PIVOTS, RENAMES
from fdb_scraper.generated import CLOSED_VOCABS
from fdb_scraper.schema import (
    COLUMNS,
    EXPORT_FIELDS,
    PIVOTED_SOURCES,
    PUBLISHED_FIELDS,
    build_schema,
)


def _failed_checks(excinfo) -> set[str]:
    return set(excinfo.value.failure_cases["check"].to_list())


def test_the_fixture_validates(df):
    assert df.height == 3
    assert df.columns == list(EXPORT_FIELDS)


def test_a_new_upstream_category_raises(df):
    drifted = df.with_columns(pl.col("funding_type").list.eval(pl.lit("neue_kategorie")))
    with pytest.raises(pandera.errors.SchemaErrors) as excinfo:
        build_schema(EXPORT_FIELDS).validate(drifted, lazy=True)
    assert "funding_type_closed_vocab" in _failed_checks(excinfo)


# Every column whose values are drawn from a closed vocabulary, named by the check the
# schema builds for it. Derived rather than listed, so a new closed vocabulary is
# covered without an edit -- and an existing one that quietly loses its check fails
# test_every_closed_vocabulary_is_checked below rather than going unnoticed.
VOCAB_COLUMNS = sorted(
    column
    for column in PUBLISHED_FIELDS
    for check in COLUMNS[column].checks
    if check.name and check.name.endswith("_closed_vocab")
)


def test_every_closed_vocabulary_is_checked():
    """The nine columns that have one, against what the config declares.

    Guards the parametrisation below -- which would pass vacuously on an empty list --
    and the enforcement itself: a vocabulary column whose check disappeared would
    silently start accepting anything, and nothing else in the suite would notice.
    """
    declared = {RENAMES.get(f, f) for f in CLOSED_VOCABS if f not in PIVOTED_SOURCES}
    assert set(VOCAB_COLUMNS) == declared | set(PIVOTS)


@pytest.mark.parametrize("column", VOCAB_COLUMNS)
def test_an_unknown_code_is_rejected_in_every_vocabulary_column(df, column):
    """Closed means closed, in all nine columns and not only ``funding_type``.

    The published values are the export's own codes and the only statement of what they
    may be is this check, so a column where it does not bite would publish whatever
    upstream started sending -- see ``fdb_scraper.generated.vocab``, which
    ``scripts/gen_vocab.py`` regenerates when that is a legitimate change.
    """
    # The fixture is already processed -- renamed and with the pivots collapsed -- so
    # every vocabulary column is present under its published name.
    assert column in df.columns
    drifted = df.with_columns(pl.col(column).list.eval(pl.lit("__not_a_code__")))
    with pytest.raises(pandera.errors.SchemaErrors) as excinfo:
        build_schema(EXPORT_FIELDS).validate(drifted, lazy=True)
    assert f"{column}_closed_vocab" in _failed_checks(excinfo)


def test_an_unresolved_list_element_fails(df):
    """list.all() ignores nulls, so without fill_null(False) this passed vacuously."""
    empty = pl.lit(None, dtype=pl.Struct({"url": pl.String, "title": pl.String}))
    unresolved = df.with_columns(further_links=pl.concat_list(empty))
    with pytest.raises(pandera.errors.SchemaErrors) as excinfo:
        build_schema(EXPORT_FIELDS).validate(unresolved, lazy=True)
    assert "further_links_resolved" in _failed_checks(excinfo)


def test_duplicate_urls_are_rejected(df):
    with pytest.raises(pandera.errors.SchemaErrors):
        build_schema(["url"]).validate(df.head(1).vstack(df.head(1)), lazy=True)


def test_the_programme_slug_is_not_required_to_be_unique(df):
    """The same programme slug exists under several funding levels.

    Which is why it is not called "id": three Länder run
    agrarinvestitionsfoerderungsprogramm as three separate programmes.
    """
    doubled = df.select("programme_slug", "id_url").head(1)
    build_schema(["programme_slug", "id_url"]).validate(doubled.vstack(doubled), lazy=True)


def test_an_unexpected_column_raises(df):
    with pytest.raises(pandera.errors.SchemaErrors):
        build_schema(["url"]).validate(df.select("url", "title"), lazy=True)


def test_an_implausible_date_is_rejected(df):
    """date_of_expiration was dropped for this; date_of_issue is still checked."""
    ancient = df.with_columns(
        date_of_issue=pl.lit("0207-12-31T23:06:32+00:00").str.to_datetime(time_zone="UTC")
    )
    with pytest.raises(pandera.errors.SchemaErrors):
        build_schema(["date_of_issue"]).validate(ancient, lazy=True)


def test_every_published_field_has_a_schema_entry():
    build_schema(PUBLISHED_FIELDS)


def test_a_subset_can_be_validated_on_its_own(export_dir):
    subset = collect(["url", "title", "funding_area"], export_dir=export_dir)
    assert subset.columns == ["url", "title", "funding_area"]


def test_unknown_published_field_is_rejected(export_dir):
    with pytest.raises(ValueError, match="unknown fields"):
        collect(["nope"], export_dir=export_dir)


def test_validation_can_be_switched_off(export_dir):
    assert collect(["url"], export_dir=export_dir, validate=False).height == 3


# --- the span invariant on the inferred column --------------------------------
# The published contract for a column no rule produces. Per-row, and across two
# columns, so it is a frame-level check rather than a column one.

SPAN_PAIR = ["keywords", "keywords_extracted"]


def _pair(keywords, extracted) -> pl.DataFrame:
    return pl.DataFrame(
        {"keywords": [keywords], "keywords_extracted": [extracted]},
        schema={"keywords": pl.String, "keywords_extracted": pl.List(pl.String)},
    )


def test_a_contiguous_partition_is_accepted():
    build_schema(SPAN_PAIR).validate(
        _pair("Erneuerbare Energien Zuschuss Kommune",
              ["Erneuerbare Energien", "Zuschuss", "Kommune"]),
        lazy=True,
    )


def test_edge_punctuation_dropped_from_a_term_is_accepted():
    """The segmenter strips a group's outer punctuation from the published term."""
    build_schema(SPAN_PAIR).validate(
        _pair("Kultur, Medien Sport", ["Kultur, Medien", "Sport"]), lazy=True
    )


@pytest.mark.parametrize(
    "keywords,extracted,why",
    [
        ("Zuschuss Kommune", ["Zuschuss", "Kommune", "Förderung"], "invented"),
        ("Zuschuss Kommune", ["Zuschuss"], "dropped"),
        ("Zuschuss Kommune", ["Kommune", "Zuschuss"], "reordered"),
        ("Zuschuss Kommune", ["Zuschuss Kommunen"], "reworded"),
        (None, ["Zuschuss"], "no source string at all"),
    ],
)
def test_a_non_span_partition_is_rejected(keywords, extracted, why):
    """What the span invariant makes unpublishable rather than merely untested.

    Boundary placement is the model's judgement and is measured; invention,
    omission, reordering and editing are structural and fail here.
    """
    with pytest.raises(pandera.errors.SchemaErrors) as excinfo:
        build_schema(SPAN_PAIR).validate(_pair(keywords, extracted), lazy=True)
    assert "keyword_spans" in _failed_checks(excinfo), why


def test_an_unsegmented_row_passes():
    """Null is how a value the segmenter has not seen is published."""
    build_schema(SPAN_PAIR).validate(_pair("Zuschuss Kommune", None), lazy=True)


def test_the_span_check_is_dropped_when_only_one_column_is_requested():
    """A subset must stay validatable; the check relates two columns."""
    build_schema(["keywords_extracted"]).validate(
        _pair("Zuschuss", ["Erfunden"]).select("keywords_extracted"), lazy=True
    )
