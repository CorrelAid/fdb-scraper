"""Writing the published tree: which file goes where, in which serialisation.

The only module here that touches the filesystem. Paths mirror the URIs they
answer for -- ``def/fdb.ttl`` is served at ``/def/fdb`` -- so the publish step
copies the tree as it is and the server needs no rules beyond content
negotiation. See ``Caddyfile``.

Every resource is written in three serialisations: Turtle for harvesters, JSON-LD
for the Linked Data clients that prefer it, HTML for a person who followed the URI
in a browser. One resource, three representations, one generator run -- so a
consumer sending ``Accept: application/ld+json`` gets the same content rather
than a 415.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from rdflib import Graph
from rdflib.namespace import RDF

from fdb_scraper.dcat.graphs import build_dataset, build_vocabulary
from fdb_scraper.dcat.pages import (
    render_dataset_html,
    render_index_html,
    render_vocabulary_html,
)
from fdb_scraper.config import DATASET, DATASET_ID, DISTRIBUTIONS, VOCAB
from fdb_scraper.dcat.table_schema import build_table_schema
from fdb_scraper.schema import PUBLISHED_FIELDS


def as_jsonld(graph: Graph) -> str:
    """``graph`` as JSON-LD, ordered so that regenerating it produces no diff.

    rdflib's JSON-LD serialiser emits nodes and their values in whatever order it
    walks the graph, which differs between two runs over the same triples. These
    files are committed, so an unordered serialisation would show up as a diff on
    every regeneration and bury the change someone actually has to review --
    exactly what ``longturtle`` is chosen for on the Turtle side.
    """
    nodes = json.loads(graph.serialize(format="json-ld"))
    for node in nodes:
        for key, value in node.items():
            if isinstance(value, list):
                node[key] = sorted(value, key=lambda v: json.dumps(v, sort_keys=True))
    nodes.sort(key=lambda n: n.get("@id", ""))
    return json.dumps(nodes, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def distribution_sizes(data_dir: Path) -> tuple[dict[str, int], list[str]]:
    """Byte size per built distribution file, plus a note per file not built.

    A missing file is not an error: the committed ``dcat/`` is generated without a
    data directory at all, and ``dcat:byteSize`` is simply left off. Reported so a
    publish run that expected the file cannot miss that it was absent.
    """
    sizes: dict[str, int] = {}
    missing: list[str] = []
    for spec in DISTRIBUTIONS:
        path = data_dir / spec["file"]
        if path.is_file():
            sizes[spec["file"]] = path.stat().st_size
        else:
            missing.append(f"not built, no byteSize: {path}")
    return sizes, missing


def write_artefacts(
    out_dir: Path, modified: datetime, sizes: dict[str, int] | None = None
) -> list[str]:
    """Write every published artefact into ``out_dir``; return one line per file set.

    ``longturtle`` sorts its output, so a regeneration that changes nothing
    produces no diff -- which is what makes the committed copy reviewable.

    The returned lines are what the CLI prints. Returned rather than printed so
    the caller decides whether a generator run is allowed to write to stdout.
    """
    sizes = sizes or {}
    out_dir.mkdir(parents=True, exist_ok=True)

    schema_out = out_dir / "table-schema.json"
    schema_out.write_text(
        json.dumps(build_table_schema(), indent=2, ensure_ascii=False) + "\n"
    )

    vocab_graph = build_vocabulary(modified)
    dataset_graph = build_dataset(modified, sizes)

    vocabulary = out_dir / "def" / "fdb.ttl"
    vocabulary.parent.mkdir(parents=True, exist_ok=True)
    vocabulary.write_bytes(vocab_graph.serialize(format="longturtle", encoding="utf-8"))
    (out_dir / "def" / "fdb.jsonld").write_text(as_jsonld(vocab_graph))
    (out_dir / "def" / "fdb.html").write_text(
        render_vocabulary_html(vocab_graph), encoding="utf-8"
    )

    dataset_doc = out_dir / "id" / "dataset" / f"{DATASET_ID}.ttl"
    dataset_doc.parent.mkdir(parents=True, exist_ok=True)
    dataset_doc.write_bytes(dataset_graph.serialize(format="longturtle", encoding="utf-8"))
    (out_dir / "id" / "dataset" / f"{DATASET_ID}.jsonld").write_text(
        as_jsonld(dataset_graph)
    )
    (out_dir / "id" / "dataset" / f"{DATASET_ID}.html").write_text(
        render_dataset_html(dataset_graph), encoding="utf-8"
    )

    # The root page, so the host root describes the dataset instead of serving
    # Caddy's directory listing.
    (out_dir / "index.html").write_text(render_index_html(dataset_graph), encoding="utf-8")

    minted = len(set(vocab_graph.subjects(RDF.type, RDF.Property)))
    return [
        f"{schema_out}: {len(PUBLISHED_FIELDS)} columns",
        f"{vocabulary}: {minted} minted terms, 1 class -> {VOCAB}",
        f"{dataset_doc}: {len(dataset_graph)} triples, "
        f"{len(DISTRIBUTIONS)} distributions -> {DATASET}",
    ]
