"""The published DCAT-AP.de metadata and the CSVW table schema.

Artefacts are written to ``dcat/`` and committed, so a change to what is
published shows up as a reviewable diff -- same arrangement as
``fdb_scraper.generated``. ``scripts/gen_dcat.py`` is the command that runs this
package; the package itself is what the tests and the pipeline import.

``id/dataset/foerderdatenbank-programme.ttl``
    What the dataset URI returns when dereferenced, and the whole harvesting
    interface: one ``dcat:Dataset`` with its ``dcat:Distribution``, publisher and
    contact point in the same graph. Self-contained on purpose -- piveau's
    ``importing-rdf`` and ckanext-dcat both parse the one document they fetch and
    never dereference a ``dcat:dataset`` link, so a description split across
    documents harvests as a dataset with no properties.

    No ``dcat:Catalog`` is published here. The catalogue that lists this dataset
    alongside the Civic Data Lab's others is a separate deployment that fetches
    this document and merges it; a catalogue node in this repository would be a
    second, competing claim about what the Civic Data Lab publishes.

    The 2500 Förderprogramme are records *inside* the distribution, not datasets
    -- publishing them as 2500 datasets would misrepresent the dataset and swamp
    the portal.

``def/fdb.ttl``
    The minted vocabulary: one ``rdf:Property`` per published column that no
    foreign term describes, plus the class those columns are properties of.
    :data:`fdb_scraper.config.VOCAB` is a hash namespace, so this one document makes
    every term in it dereferenceable.

``table-schema.json``
    The per-column contract, generated from :data:`fdb_scraper.schema.COLUMNS`
    and :mod:`fdb_scraper.semantics`, and referenced from the distribution with
    ``dct:conformsTo``. CSV carries no column metadata; DCAT-AP.de has no
    column-level vocabulary at all. So this is the only way the predicates,
    required flags, patterns and closed vocabularies the pipeline already
    enforces become visible to a consumer.

Each of the two RDF resources is also written as JSON-LD and as an HTML landing
page, and the tree gets an ``index.html`` -- see
:mod:`fdb_scraper.dcat.artefacts`.

Nothing in this package declares what is published. Titles, descriptions,
keywords, the distribution, the authority codes claimed and the per-column notes
are all in :mod:`fdb_scraper.config`, with every other hand-made decision in the
repository; these modules only turn them into documents.

The modules split by what changes for what reason:

:mod:`~fdb_scraper.dcat.profile`
    The RDF vocabularies those documents are written in: namespace objects and the
    EU authority helper.
:mod:`~fdb_scraper.dcat.columns`
    Which of the notes apply to which column, plus the per-column facts read off
    the pandera schema.
:mod:`~fdb_scraper.dcat.table_schema`
    The CSVW document, and the header check a written CSV has to pass.
:mod:`~fdb_scraper.dcat.graphs`
    The two RDF graphs.
:mod:`~fdb_scraper.dcat.pages`
    HTML for the same URIs.
:mod:`~fdb_scraper.dcat.artefacts`
    Where each file goes, and the only filesystem access in the package.

Volatile values are kept out of the prose deliberately: only ``dct:modified`` and
the per-distribution ``dcat:byteSize`` vary between runs, so regenerating after a
no-op change produces a near-empty diff.

CSV is the only distribution. Nested values (lists, structs, lists of datetimes)
are pre-encoded as JSON strings in their cell; the convention is declared per
column in the table schema so a consumer knows which cells to ``json.loads()``.
"""

from __future__ import annotations

from fdb_scraper.dcat.artefacts import distribution_sizes, write_artefacts
from fdb_scraper.dcat.columns import datatype_of, description_of, range_of
from fdb_scraper.dcat.graphs import build_dataset, build_vocabulary
from fdb_scraper.dcat.pages import (
    render_dataset_html,
    render_index_html,
    render_vocabulary_html,
)
from fdb_scraper.dcat.table_schema import build_table_schema, check_csv

__all__ = [
    "build_dataset",
    "build_table_schema",
    "build_vocabulary",
    "check_csv",
    "datatype_of",
    "description_of",
    "distribution_sizes",
    "range_of",
    "render_dataset_html",
    "render_index_html",
    "render_vocabulary_html",
    "write_artefacts",
]
