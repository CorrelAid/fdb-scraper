"""The generator has to say the same thing as the schema it generates from.

``tests/test_dcat.py`` validates the *committed* artefacts against the DCAT-AP.de
shapes -- what a harvester will see. This module checks the generator that
produces them, on the parts a profile validator cannot decide: that a cell's
declared type matches how the writer actually encodes it, that nothing the
pandera schema enforces is dropped or contradicted on the way out, and that a
regeneration of unchanged inputs produces unchanged bytes.

Kept apart from the harvesting tests because the two fail for different reasons:
a failure here is a bug in :mod:`fdb_scraper.dcat`, a failure there is usually a
committed artefact nobody regenerated.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import polars as pl
import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCAT, DCTERMS, RDF, RDFS, XSD

from fdb_scraper.dcat import (
    build_dataset,
    build_table_schema,
    build_vocabulary,
    check_csv,
    datatype_of,
    range_of,
    render_dataset_html,
    render_index_html,
    render_vocabulary_html,
    write_artefacts,
)
from fdb_scraper.dcat.artefacts import as_jsonld, distribution_sizes
from fdb_scraper.dcat.columns import list_note, polars_dtype
from fdb_scraper.config import (
    DATASET,
    DATASET_ID,
    DATASET_TITLE,
    DESCRIPTION,
    DISTRIBUTIONS,
    LICENCE_URI,
    ORIGIN_TERM,
    VOCAB,
)
from fdb_scraper.schema import COLUMNS, ORIGIN, PUBLISHED_FIELDS
from fdb_scraper.semantics import ANNOTATIONS, PREDICATES, expand

MODIFIED = datetime(2026, 1, 2, tzinfo=timezone.utc)

LIST_COLUMNS = [
    name for name in PUBLISHED_FIELDS if isinstance(polars_dtype(name), pl.List)
]
FLAT_COLUMNS = [name for name in PUBLISHED_FIELDS if name not in LIST_COLUMNS]


@pytest.fixture(scope="module")
def schema() -> dict:
    return build_table_schema()


@pytest.fixture(scope="module")
def columns(schema: dict) -> dict[str, dict]:
    return {c["name"]: c for c in schema["tableSchema"]["columns"]}


@pytest.fixture(scope="module")
def dataset_graph() -> Graph:
    return build_dataset(MODIFIED, {})


@pytest.fixture(scope="module")
def vocabulary_graph() -> Graph:
    return build_vocabulary(MODIFIED)


# --- The cell a validator parses --------------------------------------------


def test_there_are_list_columns_at_all() -> None:
    """Guards the parametrisation below: an empty list would pass those silently."""
    assert LIST_COLUMNS


@pytest.mark.parametrize("column", LIST_COLUMNS)
def test_a_list_column_declares_the_type_of_its_cell_not_its_members(column: str) -> None:
    """The cell holds one JSON array, so ``string`` is the only true datatype.

    Typing it by the member -- ``dateTime`` for ``previous_update_dates`` -- reads
    fine but makes every cell in the column invalid, ``[]`` first of all, because a
    CSVW validator parses the cell it is given. There is no separator to declare
    instead: the writer JSON-encodes the value, which is what the description says.
    """
    assert datatype_of(column) == "string"


@pytest.mark.parametrize("column", LIST_COLUMNS)
def test_a_list_column_says_its_cell_is_json_and_what_is_in_it(column: str) -> None:
    """The member type has to survive somewhere, since the datatype cannot carry it."""
    note = list_note(column)
    assert note is not None
    assert "JSON-encoded" in note
    inner = polars_dtype(column).inner
    if isinstance(inner, pl.Datetime):
        assert "xsd:dateTime" in note
    elif isinstance(inner, pl.Struct):
        for field in inner.fields:
            assert field.name in note


@pytest.mark.parametrize("column", LIST_COLUMNS)
def test_the_published_description_of_a_list_column_carries_its_encoding(
    column: str, columns: dict[str, dict]
) -> None:
    """A hand-written meaning note must not crowd the encoding out of the schema.

    ``previous_update_dates`` has both, and the encoding is the half a consumer
    cannot guess: ``datatype: string`` alone reads as one value per cell.
    """
    assert "JSON-encoded" in columns[column]["dc:description"]


def test_a_list_column_still_ranges_over_its_member_type() -> None:
    """``rdfs:range`` is per value, so it is where the member type does belong."""
    assert range_of("previous_update_dates") == XSD.dateTime
    assert range_of("funding_type") == XSD.string
    # A list of structs has no XSD type at all; no range beats a wrong one.
    assert range_of("further_links") is None


def test_a_datetime_column_carries_the_range_the_schema_checks() -> None:
    """pandera's ``in_range`` is the only statement of plausible dates; CSVW can hold it."""
    datatype = datatype_of("on_website_from")
    check = next(c for c in COLUMNS["on_website_from"].checks if c.name == "in_range")
    assert datatype["base"] == "dateTime"
    assert datatype["minimum"] == check._check_kwargs["min_value"].isoformat()
    assert datatype["maximum"] == check._check_kwargs["max_value"].isoformat()


def test_a_pattern_column_carries_the_regex_the_schema_enforces() -> None:
    datatype = datatype_of("id_hash")
    check = next(c for c in COLUMNS["id_hash"].checks if c.name == "str_matches")
    assert datatype == {"base": "string", "format": check._check_kwargs["pattern"]}


def test_the_boolean_column_is_a_boolean() -> None:
    assert datatype_of("deleted") == "boolean"


# --- The column contract as a whole -----------------------------------------


def test_every_published_column_appears_once_in_order(schema: dict) -> None:
    names = [c["name"] for c in schema["tableSchema"]["columns"]]
    assert names == list(PUBLISHED_FIELDS)


@pytest.mark.parametrize("column", list(PUBLISHED_FIELDS))
def test_each_column_names_its_predicate_and_where_its_values_come_from(
    column: str, columns: dict[str, dict]
) -> None:
    """Without the predicate a column is an unlabelled string; without the origin a
    model's reading of an upstream field reads as a fact of the source."""
    entry = columns[column]
    assert entry["propertyUrl"] == expand(PREDICATES[column])
    assert entry[ORIGIN_TERM] == ORIGIN[column]
    assert entry[ORIGIN_TERM] in {"upstream", "derived", "inferred"}


@pytest.mark.parametrize("column", list(PUBLISHED_FIELDS))
def test_required_matches_what_the_schema_allows_to_be_null(
    column: str, columns: dict[str, dict]
) -> None:
    """A column CSVW calls required and pandera lets be null is a promise nobody keeps."""
    assert columns[column].get("required", False) is (not COLUMNS[column].nullable)


def test_the_table_schema_states_the_key_and_what_a_row_is_about(schema: dict) -> None:
    table = schema["tableSchema"]
    assert table["primaryKey"] == "id_hash"
    assert table["aboutUrl"].endswith("programme/{id_url}")
    # The writer leaves a null as an empty field, which CSVW would otherwise read
    # as the empty string.
    assert table["null"] == ""
    assert "http://www.w3.org/ns/csvw" in schema["@context"]
    # fdb: has to be bound, or the per-column fdb:origin is an undefined key.
    assert schema["@context"][1]["fdb"] == VOCAB


def test_the_inferred_column_says_it_was_inferred_and_how_well(
    columns: dict[str, dict],
) -> None:
    """The one column a consumer must not read as a statement of the source."""
    entry = columns["keywords_extracted"]
    assert entry[ORIGIN_TERM] == "inferred"
    assert "inferred" in entry["dc:description"]
    assert "F1" in entry["dc:description"]


# --- The CSV a publish run actually wrote -----------------------------------


def _write_csv(path, columns) -> None:
    path.write_text(",".join(columns) + "\n")


def test_a_matching_header_reports_nothing(tmp_path) -> None:
    csv = tmp_path / "programme.csv"
    _write_csv(csv, PUBLISHED_FIELDS)
    assert check_csv(csv) == []


def test_a_missing_or_extra_column_is_named(tmp_path) -> None:
    csv = tmp_path / "programme.csv"
    _write_csv(csv, [*list(PUBLISHED_FIELDS)[:-1], "surprise"])
    problems = check_csv(csv)
    assert len(problems) == 1
    assert PUBLISHED_FIELDS[-1] in problems[0]
    assert "surprise" in problems[0]


def test_reordered_columns_are_reported_as_an_order_problem(tmp_path) -> None:
    """The same set in a different order: nothing is missing, yet position-based
    readers get every value wrong."""
    csv = tmp_path / "programme.csv"
    reordered = [PUBLISHED_FIELDS[1], PUBLISHED_FIELDS[0], *PUBLISHED_FIELDS[2:]]
    _write_csv(csv, reordered)
    assert check_csv(csv) == [
        "columns are in a different order than PUBLISHED_FIELDS"
    ]


# --- The graphs -------------------------------------------------------------


def test_the_dataset_carries_the_date_it_was_generated_with(dataset_graph: Graph) -> None:
    assert dataset_graph.value(URIRef(DATASET), DCTERMS.modified).toPython() == MODIFIED


def test_a_distribution_gets_a_bytesize_only_for_a_file_that_was_built() -> None:
    """The committed copy is generated without any data, and a made-up zero would
    tell a portal the download is empty."""
    spec = DISTRIBUTIONS[0]
    without = build_dataset(MODIFIED, {})
    assert not list(without.objects(None, DCAT.byteSize))

    with_size = build_dataset(MODIFIED, {spec["file"]: 4242})
    assert [o.toPython() for o in with_size.objects(None, DCAT.byteSize)] == [4242]


def test_the_dataset_graph_describes_everything_it_points_at(dataset_graph: Graph) -> None:
    """A harvester reads this one graph, so a publisher it cannot resolve is a blank
    field in the portal. The committed file is checked the same way in test_dcat.py;
    here it is the builder that is on the hook."""
    for predicate in (DCAT.distribution, DCAT.contactPoint, DCTERMS.publisher):
        for _, obj in dataset_graph.subject_objects(predicate):
            assert (obj, RDF.type, None) in dataset_graph, f"{obj} is undescribed"


def test_the_vocabulary_defines_exactly_the_minted_predicates(
    vocabulary_graph: Graph,
) -> None:
    defined = {str(s) for s in vocabulary_graph.subjects(RDF.type, RDF.Property)}
    minted = {
        expand(curie)
        for column, curie in PREDICATES.items()
        if column in PUBLISHED_FIELDS and curie.startswith("fdb:")
    } | {expand(curie) for curie in ANNOTATIONS}
    assert defined == minted


def test_every_minted_term_points_back_at_the_document_defining_it(
    vocabulary_graph: Graph,
) -> None:
    """The namespace is a hash namespace: one document has to answer for all of it."""
    ontology = URIRef(VOCAB.rstrip("#"))
    for term in vocabulary_graph.subjects(RDF.type, RDF.Property):
        assert (term, RDFS.isDefinedBy, ontology) in vocabulary_graph


def test_a_minted_term_repeats_what_the_column_contract_says(
    vocabulary_graph: Graph, columns: dict[str, dict]
) -> None:
    """The vocabulary and the table schema are two views of one column, so a note
    written for one has to reach the other."""
    term = URIRef(expand(PREDICATES["previous_update_dates"]))
    comments = [str(o) for o in vocabulary_graph.objects(term, RDFS.comment)]
    assert comments
    description = columns["previous_update_dates"]["dc:description"]
    assert all(comment in description for comment in comments)


# --- The written tree -------------------------------------------------------


EXPECTED_FILES = {
    "table-schema.json",
    "index.html",
    "def/fdb.ttl",
    "def/fdb.jsonld",
    "def/fdb.html",
    f"id/dataset/{DATASET_ID}.ttl",
    f"id/dataset/{DATASET_ID}.jsonld",
    f"id/dataset/{DATASET_ID}.html",
}


def test_every_resource_is_written_in_every_representation(tmp_path) -> None:
    """Each URI answers in Turtle, JSON-LD and HTML, so content negotiation has
    something to serve for whatever a client asks for."""
    write_artefacts(tmp_path, MODIFIED)
    written = {
        str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()
    }
    assert written == EXPECTED_FILES


def test_regenerating_unchanged_inputs_changes_no_bytes(tmp_path) -> None:
    """The tree is committed, so an unordered serialisation would show up as a diff
    on every run and bury the change someone has to review."""
    first, second = tmp_path / "first", tmp_path / "second"
    write_artefacts(first, MODIFIED)
    write_artefacts(second, MODIFIED)
    for name in EXPECTED_FILES:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


# rdflib's own JSON-LD parser builds a ConjunctiveGraph internally, which rdflib
# now deprecates; the warning is theirs to fix and this suite turns warnings into
# errors, so it is ignored for this one test rather than project-wide.
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_the_written_json_ld_holds_the_same_graph_as_the_turtle(tmp_path) -> None:
    """Two serialisations of one resource, or a client gets a different answer
    depending on which representation it asked for."""
    write_artefacts(tmp_path, MODIFIED)
    turtle = Graph().parse(tmp_path / "id" / "dataset" / f"{DATASET_ID}.ttl")
    jsonld = Graph().parse(
        tmp_path / "id" / "dataset" / f"{DATASET_ID}.jsonld", format="json-ld"
    )
    assert set(turtle) == set(jsonld)


def test_the_json_ld_is_sorted_rather_than_in_graph_order(dataset_graph: Graph) -> None:
    nodes = json.loads(as_jsonld(dataset_graph))
    ids = [n.get("@id", "") for n in nodes]
    assert ids == sorted(ids)


def test_a_file_that_was_not_built_is_reported_rather_than_sized(tmp_path) -> None:
    sizes, missing = distribution_sizes(tmp_path)
    assert sizes == {}
    assert len(missing) == len(DISTRIBUTIONS)
    assert DISTRIBUTIONS[0]["file"] in missing[0]

    (tmp_path / DISTRIBUTIONS[0]["file"]).write_text("a,b\n1,2\n")
    sizes, missing = distribution_sizes(tmp_path)
    assert sizes == {DISTRIBUTIONS[0]["file"]: 8}
    assert missing == []


# --- The prose the config declares as templates ------------------------------


def test_every_description_placeholder_is_filled(dataset_graph: Graph) -> None:
    """A ``{fields}`` that reached the published metadata is what this guards.

    :mod:`fdb_scraper.config` imports nothing from the package, so the counts its
    prose quotes are ``str.format`` placeholders rather than f-string expressions --
    which moves the failure from import time to publish time. A renamed placeholder
    would otherwise ship a literal brace into a harvested description.
    """
    published = [
        str(o) for o in dataset_graph.objects(URIRef(DATASET), DCTERMS.description)
    ]
    assert len(published) == len(DESCRIPTION)
    for text in published:
        assert "{" not in text and "}" not in text
    # And the count is the real one, not a plausible-looking constant.
    assert all(str(len(PUBLISHED_FIELDS)) in text for text in published)


# --- The pages a person lands on --------------------------------------------


def test_the_dataset_page_shows_the_distribution_and_its_size() -> None:
    page = render_dataset_html(build_dataset(MODIFIED, {DISTRIBUTIONS[0]["file"]: 1234}))
    # The title the metadata states, not a copy of it: what it says is an editorial
    # decision, that it reaches the page is what this test is about.
    assert f"<title>{DATASET_TITLE['de']}</title>" in page
    assert "1,234 bytes" in page
    assert DISTRIBUTIONS[0]["file"] in page
    # The licence, which hangs off the distribution rather than the dataset.
    assert LICENCE_URI in page
    # The .ttl and .jsonld of the same resource, so a reader can get the RDF.
    assert f"{DATASET}.jsonld" in page


def test_the_vocabulary_page_lists_every_term_the_document_defines(
    vocabulary_graph: Graph,
) -> None:
    page = render_vocabulary_html(vocabulary_graph)
    for term in vocabulary_graph.subjects(RDF.type, RDF.Property):
        label = str(vocabulary_graph.value(term, RDFS.label))
        assert f"<code>{label}</code>" in page


def test_the_index_page_links_to_the_data_and_the_schema(dataset_graph: Graph) -> None:
    """It replaces a directory listing, so its job is to say where to start."""
    page = render_index_html(dataset_graph)
    assert "/data/programme.csv" in page
    assert "/table-schema" in page
    assert VOCAB.rstrip("#") in page


def test_a_rendered_page_escapes_what_came_out_of_the_graph() -> None:
    """The prose is ours today, but a page that interpolates a graph value raw is one
    upstream string away from broken markup."""
    graph = build_dataset(MODIFIED, {})
    dataset = URIRef(DATASET)
    graph.remove((dataset, DCTERMS.description, None))
    graph.add((dataset, DCTERMS.description, Literal("<script>x</script>")))
    page = render_dataset_html(graph)
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
