"""Regenerate the published DCAT-AP.de metadata and the CSVW table schema.

Artefacts written to ``dcat/`` and committed so a change to what is published
shows up as a reviewable diff -- same arrangement as
``scripts/gen_vocab.py`` and ``scripts/gen_contract.py``:

``dcat/id/dataset/foerderdatenbank-programme.ttl``
    What the dataset URI returns when dereferenced, and the whole harvesting
    interface: one ``dcat:Dataset`` with its ``dcat:Distribution``, publisher and
    contact point in the same graph. Self-contained on purpose -- piveau's
    ``importing-rdf`` and ckanext-dcat both parse the one document they fetch and
    never dereference a ``dcat:dataset`` link, so a description split across
    documents harvests as a dataset with no properties.

    No ``dcat:Catalog`` is published here. The catalogue that lists this dataset
    alongside the Civic Data Lab's others is a separate deployment that fetches
    this document and merges it; a catalogue node in this repository would be a
    second, competing claim about what the Civic Data Lab publishes. ``CATALOGUE.md``
    specifies that deployment.

    The 2500 Förderprogramme are records *inside* the distribution, not datasets
    -- publishing them as 2500 datasets would misrepresent the dataset and swamp
    the portal.

``dcat/def/fdb.ttl``
    The minted vocabulary: one ``rdf:Property`` per published column that no
    foreign term describes, plus the class those columns are properties of.
    :data:`fdb_scraper.semantics.VOCAB` is a hash namespace, so this one document
    makes every term in it dereferenceable.

Both paths mirror the URIs they answer for: ``dcat/`` maps onto the server root,
so ``dcat/def/fdb.ttl`` is served at ``/def/fdb`` and negotiated there. See
``Caddyfile``.

``dcat/table-schema.json``
    The per-column contract, generated from :data:`fdb_scraper.schema.COLUMNS`
    and :mod:`fdb_scraper.semantics`, and referenced from the distribution with
    ``dct:conformsTo``. CSV carries no column metadata; DCAT-AP.de has no
    column-level vocabulary at all. So this is the only way the predicates,
    required flags, patterns and closed vocabularies the pipeline already
    enforces become visible to a consumer.

Usage::

    uv run python scripts/gen_dcat.py                      # metadata only
    uv run python scripts/gen_dcat.py --data-dir dist      # + byteSize per file

Volatile values are kept out of the prose deliberately: only ``dct:modified``
and the per-distribution ``dcat:byteSize`` vary between runs, so regenerating
after a no-op change produces a near-empty diff.

CSV is the only distribution. Nested values (lists, structs, lists of datetimes)
are pre-encoded as JSON strings in their cell; the convention is declared per
column in the table-schema so a consumer knows which cells to ``json.loads()``.
The declared columns are checked against a written file by ``--check-csv``.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS as DCT
from rdflib.namespace import RDF, RDFS, XSD

from fdb_scraper.codelists import IDENTIFIERS, LINKED
from fdb_scraper.schema import (
    PIVOT_PARENT_VOCAB,
    PIVOTED_SOURCES,
    PIVOTS,
    SEPARATOR,
    pivot_paths,
)
from fdb_scraper.schema import (
    COLUMNS,
    INFERRED_COLUMNS,
    ORIGIN,
    PUBLISHED_FIELDS,
    SOURCE_LICENSOR,
)
from fdb_scraper.semantics import (
    NAMESPACES,
    ORIGIN_TERM,
    PREDICATES,
    SCHEME_VOCAB,
    SCHEMES,
    expand,
)
from fdb_scraper.uris import (
    BASE,
    DATASET,
    DATASET_ID,
    DOWNLOAD_BASE,
    SCHEMA_URL,
    VOCAB,
)
from fdb_scraper.generated import CLOSED_VOCABS

ROOT = Path(__file__).parent.parent
OUT_DIR = ROOT / "dcat"

# --- What is published, and where -------------------------------------------
# Every URL comes from :mod:`fdb_scraper.uris`, which is the single place the
# published host is stated; nothing below constructs one from scratch.

# Where the source came from, for provenance. Cited rather than republished: the
# export itself is not a distribution here, so ``prov:wasDerivedFrom`` is the only
# thing tying a published table back to the bytes it was built from. Upstream
# serves only the current export, so that link identifies the process, not the
# exact input -- see the retention entry in README.md.
#
# Carried as ``foaf:page`` and *not* ``dct:source``: DCAT-AP 3.0 ranges
# ``dct:source`` over ``dcat:Dataset`` ("a related Dataset from which the described
# Dataset is derived"), and the Förderdatenbank's front page is a web page, not a
# catalogued dataset. 2.1.1 left the range open, so this passed then and is a
# violation now. ``foaf:page`` wants a ``foaf:Document``, which the page is, and
# which this document states so it stays self-contained.
SOURCE_HOMEPAGE = "https://www.foerderdatenbank.de/"
# Mirrors ``scraper.EXPORT_URL`` -- the endpoint that hands back the zip. Stated
# here independently so the metadata is buildable without importing the scraper.
SOURCE_EXPORT = "https://www.foerderdatenbank.de/FDB/WS/export"

# Passed through from the source, which is why it is ND: the derived table
# carries no licence of its own to grant.
LICENSE = "http://dcat-ap.de/def/licenses/cc-by-nd/4.0"
# From schema.py rather than restated: the same name goes into every row's
# license_info, and two spellings of the rights holder would be two claims about who
# owns the data.
ATTRIBUTION = SOURCE_LICENSOR

AUTHORITY = "http://publications.europa.eu/resource/authority/"

# The class the minted properties describe. One record of the table is one
# Förderprogramm; the CSVW schema's aboutUrl names the same thing.
RECORD_CLASS = "Foerderprogramm"

# How many of the linked codelists are XÖV; the remainder is NUTS. Derived so the
# dataset description cannot drift from what codelists.py actually links.
_XOEV = sum(
    IDENTIFIERS[codelist].startswith("urn:xoev-de:")
    for codelist, _ in LINKED.values()
)

# Interpolated rather than typed: the count was already wrong by five when the
# history columns were added, and a description that contradicts the schema it
# ships beside is worse than one without a number.
DESCRIPTION_DE = f"""\
Alle Förderprogramme der Förderdatenbank des Bundes (Bund, Länder und EU), ein \
Datensatz pro Programm mit {len(PUBLISHED_FIELDS)} Feldern.

Die Bundesregierung bietet mit der Förderdatenbank eine Suchmaschine für \
Förderungen an, veröffentlicht den zugrundeliegenden Datensatz aber nicht als Open Data im normativen Sinne. \

Grundlage ist der XML-Programmexport der Förderdatenbank. \
Kategorien liegen dadurch immer als interne Codes vor, nie als Anzeigenamen; die \
deutschen Labels werden mitgeliefert. {len(LINKED)} Kategoriespalten sind \
zusätzlich mit veröffentlichten Codelisten verknüpft: {_XOEV} mit XÖV-Codelisten \
(XFLB), eine mit NUTS. Verknüpft, nicht ersetzt -- die Werte bleiben die internen \
Codes des Exports, und die Zuordnung wird als SKOS-Match mitveröffentlicht, weil \
jede dieser Codelisten mindestens eine Kategorie des Exports nicht abbilden kann. \

Eine Spalte ist modellbasiert und nicht Teil der Quelle: ``keywords_extracted`` \
enthält die Stichwörter, die ein feinabgestimmtes Sprachmodell aus der \
Rohspalte ``keywords`` herausliest -- upstream sind sie durch Leerzeichen \
verbunden und in 87,7 % der Fälle nicht trennbar. Jedes Stichwort ist \
nachweislich ein zusammenhängender Abschnitt des Originalstrings, geprüft bei \
jeder Veröffentlichung; die Grenzziehung selbst ist eine Modellentscheidung \
(F1 0,967 auf einer Handstichprobe). ``keywords`` bleibt unverändert daneben \
stehen. Das Tabellenschema kennzeichnet jede Spalte mit ``fdb:origin`` als \
``upstream``, ``derived`` oder ``inferred``. \

Der Export beinhaltet immer nur den heutigen Stand. Dieser Datensatz führt daher eine \
Historie: jedes Programm trägt, wann es zuerst erfasst wurde \
(``on_website_from``), wann sich sein Inhalt zuletzt geändert hat \
(``dct:modified``) und alle bisherigen Änderungszeitpunkte \
(``previous_update_dates``). Programme, die aus der Förderdatenbank \
verschwinden, bleiben mit ihrem letzten bekannten Inhalt und \
``deleted = true`` enthalten. Der Datensatz umfasst damit mehr Programme als \
der Export und wächst über die Zeit.\
"""

# Manual translation of DESCRIPTION_DE. Keep in lockstep: edit one, edit the other.
DESCRIPTION_EN = f"""\
All funding programmes in the German federal funding database \
(Förderdatenbank; federal, state and EU programmes), one record per programme \
with {len(PUBLISHED_FIELDS)} fields.

The federal government provides the Förderdatenbank as a search engine for \
funding programmes, but does not publish the underlying dataset as Open Data in \
the normative sense.

The basis is the Förderdatenbank's XML programme export. As a result, \
categories are always internal codes rather than display names; the German \
labels are supplied alongside. {len(LINKED)} category columns are additionally \
linked to published code lists: {_XOEV} to XÖV (XFLB) code lists, one to \
NUTS. Linked, not substituted -- the values remain the export's internal \
codes, and the mapping is published as a SKOS match, because each of these \
code lists is missing at least one category that the export uses.

One column is model-produced and not part of the source: \
``keywords_extracted`` holds the keywords a fine-tuned language model reads out \
of the raw ``keywords`` column, which upstream joins with spaces and which \
carries no separator at all in 87.7% of cases. Every keyword is verifiably a \
contiguous span of the original string, checked on every publish; where the \
boundaries fall is the model's judgement (F1 0.967 on a hand-labelled sample). \
``keywords`` is published unchanged beside it. The table schema marks every \
column with ``fdb:origin`` as ``upstream``, ``derived`` or ``inferred``.

The export only ever contains today's state. This dataset therefore maintains \
a history: each programme carries when it was first recorded \
(``on_website_from``), when its content last changed (``dct:modified``) and \
all previous change timestamps (``previous_update_dates``). Programmes that \
disappear from the Förderdatenbank are retained with their last known content \
and ``deleted = true``. The dataset thus covers more programmes than the \
export and grows over time.\
"""

KEYWORDS_DE = (
    "Förderung",
    "Förderprogramme",
    "Förderdatenbank",
    "Fördermittel"
)

# One distribution. CSV with nested values JSON-encoded in their cell; the
# encoding is declared per column in the linked table-schema, so a consumer
# knows which cells to parse.
DISTRIBUTIONS = (
    {
        "slug": "csv",
        "file": "programme.csv",
        "title_de": "Förderprogramme als CSV-Datei",
        "desc_de": "Die vollständige Tabelle mit historischen Einträgen. Das verlinkte Tabellenschema beschreibt Datentypen, "
        "Pflichtfelder und Muster; Spalten mit Listenwerten enthalten JSON-Arrays als Zellenwert.",
        "file_type": "CSV",
        "media_type": "text/csv",
        "conforms_to": SCHEMA_URL,
    },
)

DCAT = Namespace("http://www.w3.org/ns/dcat#")
DCATAP = Namespace("http://data.europa.eu/r5r/")
DCATAPDE = Namespace("http://dcat-ap.de/def/dcatde/")
DCT = Namespace("http://purl.org/dc/terms/")
FDB = Namespace(VOCAB)
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
# Only the vocabulary document needs owl, so it is bound there rather than in
# semantics.NAMESPACES, which every generated file binds from.
OWL = Namespace("http://www.w3.org/2002/07/owl#")
PROV = Namespace("http://www.w3.org/ns/prov#")
VCARD = Namespace("http://www.w3.org/2006/vcard/ns#")


def _authority(scheme: str, code: str) -> URIRef:
    return URIRef(f"{AUTHORITY}{scheme}/{code}")


def build_dataset(modified: datetime, sizes: dict[str, int]) -> Graph:
    """The harvestable document: one dataset, its distribution, publisher, contact.

    Everything a consumer cannot do without is in this one graph, because that is
    the only thing a harvester reads. Both piveau's ``importing-rdf`` and
    ckanext-dcat parse the document at the configured address and take the
    ``dcat:Dataset`` subjects they find in it; neither follows a link out to fetch
    the properties from somewhere else.

    No ``dcat:Catalog``: the aggregating catalogue adds its own node and its own
    ``dcat:dataset`` link to this dataset's URI when it merges this graph.
    """
    g = Graph()
    for prefix, uri in NAMESPACES.items():
        g.bind(prefix, Namespace(uri))
    g.bind("vcard", VCARD, override=True)

    dataset = URIRef(DATASET)
    correlaid = URIRef(f"{BASE}agent/correlaid")
    contact = URIRef(f"{BASE}agent/correlaid#contact")

    g.add((correlaid, RDF.type, FOAF.Agent))
    g.add((correlaid, FOAF.name, Literal("CorrelAid e.V.", lang="de")))
    g.add((correlaid, FOAF.homepage, URIRef("https://correlaid.org/")))
    g.add((correlaid, FOAF.mbox, URIRef("mailto:info@correlaid.org")))

    # DCAT-AP requires a vcard:Kind. Organization is a subclass of it, but the
    # shapes check the asserted type, so state both rather than rely on a
    # validator having the vCard ontology loaded.
    g.add((contact, RDF.type, VCARD.Kind))
    g.add((contact, RDF.type, VCARD.Organization))
    g.add((contact, VCARD.fn, Literal("CorrelAid e.V. -- Civic Data Lab", lang="de")))
    g.add((contact, VCARD.hasEmail, URIRef("mailto:info@correlaid.org")))
    g.add((contact, VCARD.hasURL, URIRef("https://correlaid.org/")))

    g.add((dataset, RDF.type, DCAT.Dataset))
    g.add((dataset, DCT.identifier, Literal(DATASET_ID)))
    g.add((dataset, DCT["title"], Literal("Förderdatenbank -- Förderprogramme", lang="de")))
    g.add((dataset, DCT["title"], Literal("German funding database -- programmes", lang="en")))
    g.add((dataset, DCT.description, Literal(DESCRIPTION_DE, lang="de")))
    g.add((dataset, DCT.description, Literal(DESCRIPTION_EN, lang="en")))
    g.add((dataset, DCT.publisher, correlaid))
    g.add((dataset, DCT.creator, correlaid))
    g.add((dataset, DCAT.contactPoint, contact))
    g.add((dataset, DCT.language, _authority("language", "DEU")))
    g.add((dataset, DCT.accessRights, _authority("access-right", "PUBLIC")))
    # Weekly: the export is refetched and the pipeline rerun on a schedule.
    g.add((dataset, DCT.accrualPeriodicity, _authority("frequency", "WEEKLY")))
    g.add((dataset, DCATAP.availability, _authority("planned-availability", "AVAILABLE")))
    g.add((dataset, DCT.modified, Literal(modified, datatype=XSD.dateTime)))
    # Funding programmes are run by the federal government, the Länder and the
    # EU, so the dataset spans both German political levels.
    for level in ("federal", "state"):
        g.add((
            dataset,
            DCATAPDE.politicalGeocodingLevelURI,
            URIRef(f"http://dcat-ap.de/def/politicalGeocoding/Level/{level}"),
        ))
    for theme in ("ECON", "GOVE"):
        g.add((dataset, DCAT.theme, _authority("data-theme", theme)))
    for kw in KEYWORDS_DE:
        g.add((dataset, DCAT.keyword, Literal(kw, lang="de")))
    g.add((dataset, DCAT.landingPage, URIRef("https://github.com/CorrelAid/fdb_scraper")))
    source_page = URIRef(SOURCE_HOMEPAGE)
    g.add((dataset, FOAF.page, source_page))
    g.add((source_page, RDF.type, FOAF.Document))
    g.add((dataset, PROV.wasDerivedFrom, URIRef(SOURCE_EXPORT)))
    # The column-level contract. DCAT-AP.de has no vocabulary for it, so it
    # hangs off the dataset as a conformance target and off the CSV
    # distribution as its concrete schema.
    g.add((dataset, DCT.conformsTo, URIRef(SCHEMA_URL)))

    for spec in DISTRIBUTIONS:
        dist = URIRef(f"{DATASET}/distribution/{spec['slug']}")
        url = URIRef(DOWNLOAD_BASE + spec["file"])
        g.add((dataset, DCAT.distribution, dist))
        g.add((dist, RDF.type, DCAT.Distribution))
        g.add((dist, DCT["title"], Literal(spec["title_de"], lang="de")))
        g.add((dist, DCT.description, Literal(spec["desc_de"], lang="de")))
        g.add((dist, DCAT.accessURL, url))
        g.add((dist, DCAT.downloadURL, url))
        g.add((dist, DCT["format"], _authority("file-type", spec["file_type"])))
        g.add((
            dist,
            DCAT.mediaType,
            URIRef(f"https://www.iana.org/assignments/media-types/{spec['media_type']}"),
        ))
        g.add((dist, DCT.license, URIRef(LICENSE)))
        g.add((dist, DCATAPDE.licenseAttributionByText, Literal(ATTRIBUTION, lang="de")))
        g.add((dist, DCATAP.availability, _authority("planned-availability", "AVAILABLE")))
        g.add((dist, DCT.modified, Literal(modified, datatype=XSD.dateTime)))
        if spec["conforms_to"]:
            g.add((dist, DCT.conformsTo, URIRef(spec["conforms_to"])))
        if (size := sizes.get(spec["file"])) is not None:
            g.add((dist, DCAT.byteSize, Literal(size, datatype=XSD.nonNegativeInteger)))

    return g


def build_vocabulary(modified: datetime) -> Graph:
    """The terms this dataset had to mint, as one dereferenceable document.

    :data:`fdb_scraper.semantics.VOCAB` is a hash namespace, so every term in it
    is a fragment of a single document -- one file makes the lot resolve, with no
    per-term hosting and no redirects.

    Only minted terms appear. The columns :data:`fdb_scraper.semantics.EXTERNAL`
    maps onto ``dct:``, ``foaf:`` or ``vcard:`` terms are described by whoever
    publishes those namespaces; restating them here would assert authority over
    someone else's vocabulary.

    Closed vocabularies are named in a comment rather than enumerated. Their
    codes have no URIs -- see the note on ``aboutUrl`` in
    :func:`build_table_schema` -- so a ``rdfs:range`` pointing at a concept
    scheme would be a link to nothing.
    """
    g = Graph()
    for prefix, uri in NAMESPACES.items():
        g.bind(prefix, Namespace(uri))
    g.bind("owl", OWL)

    # The namespace is "...fdb#"; the document describing it is "...fdb".
    ontology = URIRef(VOCAB.rstrip("#"))
    g.add((ontology, RDF.type, OWL.Ontology))
    g.add((ontology, DCT["title"], Literal("Förderdatenbank -- geprägte Begriffe", lang="de")))
    g.add((
        ontology,
        DCT.description,
        Literal(
            "Begriffe für die Spalten der Förderprogramm-Tabelle, für die kein "
            "etablierter Begriff passt. Generiert aus fdb_scraper.semantics.",
            lang="de",
        ),
    ))
    g.add((ontology, DCT.publisher, URIRef(f"{BASE}agent/correlaid")))
    g.add((ontology, DCT.modified, Literal(modified.date(), datatype=XSD.date)))
    g.add((ontology, DCT.source, URIRef(SOURCE_HOMEPAGE)))
    g.add((ontology, RDFS.seeAlso, URIRef(DATASET)))

    record = FDB[RECORD_CLASS]
    g.add((record, RDF.type, RDFS.Class))
    g.add((record, RDFS.label, Literal("Förderprogramm", lang="de")))
    g.add((record, RDFS.label, Literal("funding programme", lang="en")))
    g.add((
        record,
        RDFS.comment,
        Literal(
            "Ein Förderprogramm der Förderdatenbank des Bundes; eine Zeile der "
            "veröffentlichten Tabelle.",
            lang="de",
        ),
    ))
    g.add((record, RDFS.isDefinedBy, ontology))

    # The one term that describes a column rather than holding a value of one. It
    # is what the CSVW schema annotates every column with, so it has to resolve in
    # this document like any other minted term.
    origin_term = URIRef(expand(ORIGIN_TERM))
    g.add((origin_term, RDF.type, RDF.Property))
    g.add((origin_term, RDFS.label, Literal("origin")))
    g.add((origin_term, RDFS.range, XSD.string))
    g.add((origin_term, RDFS.isDefinedBy, ontology))
    g.add((
        origin_term,
        RDFS.comment,
        Literal(
            "Where a column's values come from: \"upstream\" -- stated by the "
            "source export and only reshaped; \"derived\" -- computed from upstream "
            "values or from the load history by a rule, exactly and reproducibly; "
            "\"inferred\" -- produced by a machine-learning model, therefore neither "
            "deterministic nor exact. The inferred columns are "
            f"{', '.join(INFERRED_COLUMNS)}; their accuracy is measured against a "
            "labelled sample rather than guaranteed.",
            lang="en",
        ),
    ))

    for column in PUBLISHED_FIELDS:
        curie = PREDICATES[column]
        if not curie.startswith("fdb:"):
            continue  # a foreign term already says it
        term = URIRef(expand(curie))
        g.add((term, RDF.type, RDF.Property))
        g.add((term, RDFS.label, Literal(column)))
        g.add((term, RDFS.domain, record))
        g.add((term, RDFS.isDefinedBy, ontology))
        if (rng := _range(column)) is not None:
            g.add((term, RDFS.range, rng))
        g.add((term, FDB.origin, Literal(ORIGIN[column])))
        for note in (_vocab_note(column), _inferred_note(column)):
            if note is not None:
                g.add((term, RDFS.comment, Literal(note, lang="en")))

    return g


def _polars_dtype(column: str):
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


def _range(column: str) -> URIRef | None:
    """``rdfs:range`` for a minted term, from the column's dtype.

    A list column ranges over its member type: the range of a property is what
    each of its values is, and each value of a list column is one element.
    ``further_links`` is a list of structs, which no XSD datatype describes, so it
    gets no range rather than a wrong one -- its encoding is stated in the CSVW
    schema instead.
    """
    dtype = _polars_dtype(column)
    if isinstance(dtype, pl.List):
        dtype = dtype.inner
    return _xsd(dtype)


# What an inferred column is, said in the two places a consumer looks: the CSVW
# column description and the minted term's comment. Per column, because the next
# inferred column will not be inferred the same way or to the same accuracy.
INFERRED_NOTES = {
    "keywords_extracted": (
        "inferred, not stated by the source: the raw keywords string split into "
        "single keywords by a fine-tuned German encoder, because upstream joins "
        "them with spaces and 87.7% of values carry no separator at all. Every "
        "keyword is a contiguous span of the raw string and the spans cover it "
        "exactly, which is checked on every publish, so nothing is invented, "
        "dropped or reordered; where the boundaries fall is the model's judgement "
        "-- exact-span F1 0.967 on a held-out hand-labelled sample, against 0.878 "
        "for splitting on nothing. Null where a value has not been segmented. The "
        "raw keywords column is published unchanged beside it."
    ),
}


def _inferred_note(column: str) -> str | None:
    """What produced an inferred column and how well, or None if a rule produced it."""
    return INFERRED_NOTES.get(column)


def _vocab_note(column: str) -> str | None:
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


def _datatype(column: str) -> dict | str | None:
    """Datatype for a published column, carrying over the schema's checks.

    Pandera's checks store their arguments, so ``str_matches`` becomes a CSVW
    ``format`` regex and ``in_range`` becomes ``minimum``/``maximum``. What CSVW
    cannot express is the closed vocabularies -- it has no enumeration -- so
    those are named in the column description instead, by :func:`_vocab_note`.

    A list column is typed by its member, the same reasoning as :func:`_range`:
    the type describes one value, and one value of a list column is one element.
    ``further_links`` is a list of structs, which no XSD datatype describes, so it
    gets none -- :func:`_list_note` says what it holds instead.
    """
    col = COLUMNS[column]
    dtype = _polars_dtype(column)
    if isinstance(dtype, pl.List):
        dtype = dtype.inner
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


def _list_note(column: str) -> str | None:
    """Multiplicity of a list column, or None if the column holds one value.

    Stated in prose because the schema no longer describes a flat file: without a
    separator convention there is no CSVW construct for "many values per row", and
    inventing one would describe an encoding nobody produces.
    """
    dtype = _polars_dtype(column)
    if not isinstance(dtype, pl.List):
        return None
    if isinstance(dtype.inner, pl.Struct):
        fields = ", ".join(f.name for f in dtype.inner.fields)
        return f"zero or more objects per row, each with {fields}"
    return "zero or more values per row"


def build_table_schema() -> dict:
    """The column contract, generated from the pandera schema.

    CSVW's ``tableSchema`` with no ``url``: a schema, not a description of one
    file. CSV carries no column types at all, so this is what tells a consumer
    which predicate a column denotes, which are required, what pattern a value
    matches and which range a date falls in. DCAT-AP.de has no column-level
    vocabulary of its own, so CSVW is used as the least surprising container for
    that contract.

    Nested columns (lists, structs, lists of datetimes) carry JSON-encoded
    values inside their cell. The column-level ``dc:description`` states which
    cells a consumer must ``json.loads()``.

    Every column also carries ``fdb:origin`` -- ``upstream``, ``derived`` or
    ``inferred``. CSVW has no term for it and no CSV can show it, and one published
    column is a model's reading of an upstream string rather than something the
    export states; a consumer treating that value as a fact of the source would be
    wrong in a way nothing else in the file corrects.
    """
    columns = []
    for name in PUBLISHED_FIELDS:
        col = COLUMNS[name]
        entry: dict = {
            "name": name,
            "titles": name,
            "propertyUrl": expand(PREDICATES[name]),
            ORIGIN_TERM: ORIGIN[name],
        }
        if (datatype := _datatype(name)) is not None:
            entry["datatype"] = datatype
        if not col.nullable:
            entry["required"] = True
        # No valueUrl on the closed-vocabulary columns. It would have to name a URI
        # per code, and the codes are published as codes: a value is checked
        # against fdb_scraper.generated.vocab, not against a scheme anyone can dereference.
        notes = [
            n
            for n in (_list_note(name), _vocab_note(name), _inferred_note(name))
            if n is not None
        ]
        if notes:
            entry["dc:description"] = "; ".join(notes)
        columns.append(entry)

    return {
        # fdb: is bound here so the per-column fdb:origin resolves to the minted
        # term rather than being an undefined key a consumer has to guess at.
        "@context": [
            "http://www.w3.org/ns/csvw",
            {"@language": "de", "fdb": VOCAB},
        ],
        "dc:title": "Förderdatenbank -- Förderprogramme",
        "dc:description": (
            "Spaltenvertrag der CSV-Distribution, generiert aus dem "
            "Pandera-Schema der Pipeline (scripts/gen_dcat.py)."
        ),
        "tableSchema": {
            # id_hash is the only column the schema declares unique that is not
            # derived from a URL; url is unique too but unwieldy as a key.
            "primaryKey": "id_hash",
            # Names what each row is about. Unlike the dataset and vocabulary
            # URIs, these 2500 do not resolve -- one document per programme is a
            # publishing decision not taken. The URI is still the right thing to
            # state: it is how two consumers agree which programme they mean.
            "aboutUrl": BASE + "programme/{id_url}",
            "null": "",
            "columns": columns,
        },
    }


def check_csv(path: Path) -> list[str]:
    """Report where a written CSV contradicts the generated schema.

    Reads the header only, so it costs nothing to run as a publish gate. Checks
    what the metadata asserts and the writer can be wrong about: the columns
    present and their order. Values are the pandera schema's job and are
    already checked before anything is written.
    """
    problems = []
    with path.open() as f:
        header = f.readline().rstrip("\n").rstrip("\r").split(",")
    columns = [c for c in header if c]
    if columns != list(PUBLISHED_FIELDS):
        missing = set(PUBLISHED_FIELDS) - set(columns)
        extra = set(columns) - set(PUBLISHED_FIELDS)
        if missing or extra:
            problems.append(
                f"column mismatch: missing {sorted(missing)}, extra {sorted(extra)}"
            )
        else:
            problems.append("columns are in a different order than PUBLISHED_FIELDS")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--data-dir",
        type=Path,
        help="Directory holding the built distribution files, for dcat:byteSize.",
    )
    ap.add_argument(
        "--modified",
        type=date.fromisoformat,
        help="dct:modified date (default: today, UTC).",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        help="Where to write the artefacts. Defaults to dcat/, the committed and "
        "reviewable copy. A publish run points this at its staging directory "
        "instead, so the byteSize it needs does not show up as a repo diff.",
    )
    ap.add_argument(
        "--check-csv",
        type=Path,
        help="Check a written CSV against the generated schema and exit non-zero "
        "on a mismatch.",
    )
    args = ap.parse_args()

    if args.check_csv:
        problems = check_csv(args.check_csv)
        for p in problems:
            print(f"csv: {p}")
        raise SystemExit(1 if problems else 0)

    day = args.modified or datetime.now(timezone.utc).date()
    modified = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)

    sizes = {}
    if args.data_dir:
        for spec in DISTRIBUTIONS:
            f = args.data_dir / spec["file"]
            if f.is_file():
                sizes[spec["file"]] = f.stat().st_size
            else:
                print(f"not built, no byteSize: {f}")

    out_dir = args.out_dir or OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    schema_out = out_dir / "table-schema.json"
    schema_out.write_text(
        json.dumps(build_table_schema(), indent=2, ensure_ascii=False) + "\n"
    )
    print(f"{schema_out}: {len(PUBLISHED_FIELDS)} columns")

    # The two dereferenceable documents. Their paths mirror their URIs, so the
    # publish step copies the trees as they are and the server needs no rules
    # beyond content negotiation.
    vocabulary = out_dir / "def" / "fdb.ttl"
    vocabulary.parent.mkdir(parents=True, exist_ok=True)
    vocab_graph = build_vocabulary(modified)
    vocabulary.write_bytes(vocab_graph.serialize(format="longturtle", encoding="utf-8"))
    minted = len(set(vocab_graph.subjects(RDF.type, RDF.Property)))
    print(f"{vocabulary}: {minted} minted terms, 1 class -> {VOCAB}")

    dataset_doc = out_dir / "id" / "dataset" / f"{DATASET_ID}.ttl"
    dataset_doc.parent.mkdir(parents=True, exist_ok=True)
    # longturtle sorts its output, so a regeneration that changes nothing
    # produces no diff.
    dataset_graph = build_dataset(modified, sizes)
    dataset_doc.write_bytes(dataset_graph.serialize(format="longturtle", encoding="utf-8"))
    print(
        f"{dataset_doc}: {len(dataset_graph)} triples, "
        f"{len(DISTRIBUTIONS)} distributions -> {DATASET}"
    )


if __name__ == "__main__":
    main()
