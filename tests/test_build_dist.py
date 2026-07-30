"""The publish step end to end: history in, served tree out.

``tests/test_history.py`` checks what the history means; this checks that a run
produces the files the deployment serves, in the paths the published URIs promise,
with the metadata agreeing with the CSV beside it.

No network: the fixture export is loaded first and the build runs with
``--no-ingest``. The download half of ``build_dist`` is the one thing here that no
test covers, because covering it would mean fetching 28 MB from a federal server on
every run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest
from rdflib import Graph, URIRef
from rdflib.namespace import DCAT, RDF

from fdb_scraper.history import dlt_pipeline, load
from fdb_scraper.schema import PUBLISHED_FIELDS

ROOT = Path(__file__).parent.parent
FIXTURE = Path(__file__).parent / "fixtures" / "export"
BUILD_DIST = ROOT / "scripts" / "build_dist.py"

# What the served tree has to contain, keyed by the URL each path answers for.
SERVED = {
    "data/programme.csv": "/data/programme.csv",
    "def/fdb.ttl": "/def/fdb",
    "id/dataset/foerderdatenbank-programme.ttl": "/id/dataset/foerderdatenbank-programme",
    "table-schema.json": "/table-schema.json",
}

DATASET_DOC = "id/dataset/foerderdatenbank-programme.ttl"
DATASET = URIRef("https://fdb.correlaid.org/id/dataset/foerderdatenbank-programme")


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    """A staging tree built from the fixture export, once for the module."""
    tmp = tmp_path_factory.mktemp("build")
    db = tmp / "fdb.duckdb"
    env = {"DLT_DATA_DIR": str(tmp / "dlt"), "FDB_DB": str(db)}

    # Loaded in-process so the fixture path can be passed; the build itself runs as
    # a subprocess, which is how the deployment invokes it.
    import os

    os.environ.update(env)
    os.environ.pop("POSTGRES_CONN_STR", None)
    load(FIXTURE, pipe=dlt_pipeline(db))

    out = tmp / "dist"
    subprocess.run(
        [
            sys.executable,
            str(BUILD_DIST),
            "--no-ingest",
            "--out",
            str(out),
            "--db",
            str(db),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        env={**os.environ, **env},
    )
    return out


def test_the_build_writes_every_served_path(built: Path) -> None:
    missing = [rel for rel in SERVED if not (built / rel).is_file()]
    assert not missing, f"not built: {missing}"


def test_the_csv_holds_the_published_columns(built: Path) -> None:
    """What is served must be what the schema promises, in order."""
    with (built / "data" / "programme.csv").open() as f:
        columns = f.readline().rstrip().rstrip("\r").split(",")
    assert columns == list(PUBLISHED_FIELDS)


def test_the_metadata_describes_the_csv_that_was_built(built: Path) -> None:
    """``dcat:byteSize`` has to match the file, which is why it is generated here.

    The committed ``dcat/`` copy deliberately carries no byteSize -- it would churn
    on every run. So this is the assertion that the published metadata is not the
    committed metadata with a stale number.
    """
    csv = built / "data" / "programme.csv"
    graph = Graph().parse(built / DATASET_DOC, format="turtle")

    sizes = [int(o) for o in graph.objects(None, DCAT.byteSize)]
    assert sizes == [csv.stat().st_size], "byteSize does not match the built file"


def test_the_published_dataset_document_is_harvestable(built: Path) -> None:
    """The one document an aggregating catalogue fetches, in the served tree.

    Self-containment is asserted in ``test_dcat.py`` against the committed copy;
    here the point is that a publish run writes the same graph and not a stub.
    """
    doc = Graph().parse(built / DATASET_DOC, format="turtle")
    assert (DATASET, DCAT.distribution, None) in doc
    assert not list(doc.subjects(RDF.type, DCAT.Catalog)), "no catalogue is published"


def test_the_table_schema_covers_the_csv(built: Path) -> None:
    schema = json.loads((built / "table-schema.json").read_text())
    names = [c["name"] for c in schema["tableSchema"]["columns"]]
    assert names == list(PUBLISHED_FIELDS)


def test_the_build_leaves_the_committed_metadata_alone(built: Path) -> None:
    """A publish run must not dirty ``dcat/``.

    ``dcat/`` is the reviewable artefact and is asserted up to date by
    ``test_dcat.py``. If a build wrote byteSize into it, every run would show a diff
    and that test would fail on the next commit.
    """
    committed = Graph().parse(ROOT / "dcat" / DATASET_DOC, format="turtle")
    assert list(committed.objects(None, DCAT.byteSize)) == []


def test_the_csv_is_not_left_half_written(built: Path) -> None:
    """The build writes to a temporary name and renames, so no partial file remains."""
    leftovers = list((built / "data").glob(".*incoming*"))
    assert not leftovers, f"staging files left behind: {leftovers}"
