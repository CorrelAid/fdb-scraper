"""The published metadata has to satisfy the profile the harvester expects.

``dcat/id/dataset/foerderdatenbank-programme.ttl`` is the whole harvesting
interface: piveau's ``importing-rdf`` module fetches that one file, and the
Datenatlas instance runs the DCAT-AP.de profile (its records carry ``dcatapde:``
and ``dcatap:`` terms and EU authority vocabularies throughout). So the file is
validated here against the official DCAT-AP.de 3.0 SHACL shapes rather than
eyeballed -- 3.0 because that is what GovData's validator now runs and what the
aggregating catalogue gates its merge with, and a document that passes here has to
pass there too.

No ``dcat:Catalog`` is published from this repository, so no catalogue shape has a
focus node here. The catalogue that lists this dataset is a separate deployment
that fetches this document; what this file has to guarantee in its place is that
the document stands alone -- see
:func:`test_the_dataset_document_is_self_contained`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pyshacl import validate
from rdflib import Graph, URIRef
from rdflib.namespace import DCAT, DCTERMS, RDF, SH, SKOS

from fdb_scraper.schema import PUBLISHED_FIELDS
from fdb_scraper.config import VOCAB
from fdb_scraper.semantics import ANNOTATIONS, PREDICATES, expand

ROOT = Path(__file__).parent.parent
TABLE_SCHEMA = ROOT / "dcat" / "table-schema.json"
VOCABULARY = ROOT / "dcat" / "def" / "fdb.ttl"
DATASET_DOC = ROOT / "dcat" / "id" / "dataset" / "foerderdatenbank-programme.ttl"
# Namespace the project mints its own identifiers under. Used to tell a shape
# result about our own graph from one about a remote vocabulary we did not load.
#
# Written out rather than imported from fdb_scraper.config. These are published
# identifiers: importing them would make the assertions restate whatever the
# constant happens to say, and a changed host would pass silently. Spelled here,
# moving the host fails these tests until someone confirms the move on purpose.
OWN = "https://fdb.cdl.correlaid.org/id/"
DATASET = f"{OWN}dataset/foerderdatenbank-programme"
SHAPES_DIR = Path(__file__).parent / "fixtures" / "shapes"

# The "DCAT-AP.de 3.0 - Spezifikation" profile, which is two upstreams: SEMIC's
# DCAT-AP 3.0 shapes plus the German files that translate, restrict and extend
# them. Both halves are load-bearing -- dcat-ap-SHACL-DE.ttl deactivates and adds
# shapes rather than restating the base, so the German files alone pass an empty
# dcat:Dataset. tests/fixtures/shapes/README.md says which file does what.
#
# dcat-ap-de-imports.ttl is left out: no shapes of its own, only owl:imports of
# remote controlled vocabularies. Skipping it keeps the test offline, at the cost
# of not checking that each authority URI really is a concept in its vocabulary --
# run the file through https://www.itb.ec.europa.eu/shacl/dcat-ap.de/upload before
# publishing for that.
SHAPE_FILES = (
    "dcat-ap-SHACL.ttl",
    "dcat-ap-SHACL-DE.ttl",
    "dcat-ap-de-controlledvocabularies.ttl",
    "dcat-ap-spec-german-additions.ttl",
    "dcat-ap-de-deprecated.ttl",
)


@pytest.fixture(scope="module")
def shapes() -> Graph:
    g = Graph()
    for name in SHAPE_FILES:
        g.parse(SHAPES_DIR / name, format="turtle")
    return g


@pytest.fixture(scope="module")
def published() -> Graph:
    return Graph().parse(DATASET_DOC, format="turtle")


def test_published_metadata_conforms_to_dcat_ap_de(published: Graph, shapes: Graph) -> None:
    """Every violation the shapes can decide without the remote vocabularies.

    Results on ``skos:inScheme`` are excluded, and only for focus nodes outside
    :data:`OWN`. Those shapes ask "is this IRI a concept in the vocabulary it
    comes from", which cannot be answered from a graph that does not contain the
    vocabulary, so offline they fail for every authority IRI regardless of
    whether it is right. They are a check on the vocabularies, not on this
    generator. The ITB validator resolves the imports and does decide them.

    The namespace condition keeps that exemption off our own terms: anything this
    project mints ships in the same graph, so the shape would be decidable and
    its result real. It exists so that adding one of our own documents to what is
    validated cannot silently exempt the part we are responsible for.
    """
    _, results, text = validate(
        published,
        shacl_graph=shapes,
        advanced=True,
        # No inference: the shapes are written against the asserted triples, and
        # RDFS closure over an incomplete import set only invents new failures.
        inference="none",
    )
    undecidable = 0
    violations = []
    for result in results.subjects(SH.resultSeverity, SH.Violation):
        focus = results.value(result, SH.focusNode)
        if results.value(result, SH.resultPath) == SKOS.inScheme and not str(
            focus
        ).startswith(OWN):
            undecidable += 1
            continue
        violations.append(
            f"{results.value(result, SH.focusNode)} "
            f"{results.value(result, SH.resultPath)}: "
            f"{results.value(result, SH.resultMessage)}"
        )
    assert not violations, "\n".join(violations) + f"\n\nfull report:\n{text}"
    # Fails loudly if an upstream shapes update stops producing them, which
    # would mean the exclusion above is now hiding something real.
    assert undecidable, "no skos:inScheme results -- is the exclusion still needed?"


def test_predicates_are_distinct() -> None:
    """Two columns sharing a predicate would silently merge in any RDF output."""
    seen: dict[str, str] = {}
    clashes = []
    for column, curie in PREDICATES.items():
        if (other := seen.setdefault(curie, column)) != column:
            clashes.append(f"{other} and {column} both map to {curie}")
    assert not clashes, clashes


def test_table_schema_covers_every_published_column() -> None:
    schema = json.loads(TABLE_SCHEMA.read_text())
    columns = schema["tableSchema"]["columns"]
    assert [c["name"] for c in columns] == list(PUBLISHED_FIELDS)
    assert all(c["propertyUrl"] for c in columns)


def test_every_minted_term_is_defined() -> None:
    """A minted term with no definition is a dead link in published metadata.

    The vocabulary document is the only thing standing behind the ``fdb:``
    namespace, so a new published column that gets a minted predicate has to
    arrive here too -- which it does automatically, unless someone stops
    regenerating.
    """
    g = Graph().parse(VOCABULARY, format="turtle")
    defined = {str(s) for s in g.subjects(RDF.type, RDF.Property)}
    # The column predicates, plus the terms that describe a column rather than hold
    # a value of one -- fdb:origin, which the CSVW schema puts on every column.
    minted = {
        expand(curie) for curie in PREDICATES.values() if curie.startswith("fdb:")
    } | {expand(curie) for curie in ANNOTATIONS}
    assert minted, "no minted terms at all -- has EXTERNAL swallowed every column?"
    assert minted - defined == set(), f"undefined: {sorted(minted - defined)}"
    # The reverse too: a term left behind by a renamed column would resolve to a
    # definition of something no longer published.
    assert defined - minted == set(), f"stale: {sorted(defined - minted)}"


def test_every_minted_term_resolves_within_the_vocabulary_document() -> None:
    """The namespace is a hash namespace, so one document must cover all of it."""
    doc = VOCAB.rstrip("#")
    g = Graph().parse(VOCABULARY, format="turtle")
    for term in g.subjects(RDF.type, RDF.Property):
        assert str(term).startswith(VOCAB), f"{term} is outside {VOCAB}"
        assert str(term).split("#")[0] == doc, f"{term} does not resolve to {doc}"


def test_the_dataset_document_is_self_contained(published: Graph) -> None:
    """One fetch has to yield the whole description, because that is all a harvester does.

    piveau's ``importing-rdf`` and ckanext-dcat both parse the document at the
    configured address and take the ``dcat:Dataset`` subjects out of that graph.
    Neither dereferences a URI to collect properties from a second document. So a
    node this dataset points at with a structural property has to be described in
    the same file, or the aggregating catalogue merges a dataset with a dangling
    publisher and the portal shows a blank field.

    Only our own identifiers are required to be described: the EU authority IRIs
    and the licence URIs are somebody else's to publish, and restating them here
    would assert authority over another namespace.
    """
    dataset = URIRef(DATASET)
    assert (dataset, RDF.type, DCAT.Dataset) in published, "the dataset describes itself"

    structural = (
        DCAT.distribution,
        DCAT.contactPoint,
        DCTERMS.publisher,
        DCTERMS.creator,
    )
    dangling = [
        f"{s} {p} {o}"
        for p in structural
        for s, o in published.subject_objects(p)
        if str(o).startswith(OWN) and (o, RDF.type, None) not in published
    ]
    assert not dangling, "referenced but not described:\n" + "\n".join(dangling)

    # A distribution the portal cannot download is a catalogue entry with no data
    # behind it, which is the one failure a consumer notices immediately.
    for dist in published.objects(dataset, DCAT.distribution):
        assert (dist, DCAT.downloadURL, None) in published, f"{dist} has no downloadURL"


def test_no_catalogue_is_published(published: Graph) -> None:
    """The catalogue is a separate deployment's to publish, not this one's.

    Two ``dcat:Catalog`` nodes claiming to be the Civic Data Lab's catalogue --
    one here, one in the aggregator -- is how a portal ends up harvesting the same
    dataset twice under two parents. The aggregator adds the catalogue node and
    the ``dcat:dataset`` link when it merges this graph.
    """
    catalogues = list(published.subjects(RDF.type, DCAT.Catalog))
    assert not catalogues, f"a catalogue crept back in: {catalogues}"
    assert not list(published.objects(None, DCAT.dataset)), (
        "dcat:dataset belongs to the aggregating catalogue"
    )


def test_generated_artefacts_are_up_to_date() -> None:
    """Regenerating must not change the committed files.

    Only ``dct:modified`` and ``dcat:byteSize`` are allowed to vary between
    runs, so the committed date is passed back in; anything else that moved is a
    file someone edited by hand instead of regenerating.
    """
    doc = Graph().parse(DATASET_DOC, format="turtle")
    modified = doc.value(URIRef(DATASET), DCTERMS.modified).toPython().date().isoformat()
    before = {p: p.read_bytes() for p in (TABLE_SCHEMA, VOCABULARY, DATASET_DOC)}
    subprocess.run(
        [sys.executable, "scripts/gen_dcat.py", "--modified", modified],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    try:
        for path, content in before.items():
            assert path.read_bytes() == content, f"{path.name} is stale, regenerate it"
    finally:
        for path, content in before.items():
            path.write_bytes(content)
