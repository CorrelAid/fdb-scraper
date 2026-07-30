"""The one production path no other test touches: fetching the real export.

Everything else runs against ``tests/fixtures/export``, three programmes copied out
of a real download. That covers parsing, the history and the publish step, but not
the first thing a weekly run does -- and a download that changes shape is exactly
the failure the rest of the suite cannot see.

Marked ``network`` and deselected by default, because 28 MB against a federal server
is not something a test run should do incidentally::

    uv run pytest -m network                 # before wiring the schedule
    uv run pytest -m network -k contract     # just the structural check

What is asserted is deliberately about shape rather than values. The export changes
every week, so pinning a programme count or a particular title would make this fail
for the wrong reason. What must hold is that the archive is still an archive, the
recorded structure still describes it, and the pipeline still produces a validating
table from it.
"""

from __future__ import annotations

import pytest

from fdb_scraper import collect
from fdb_scraper.contract import check_export
from fdb_scraper.history import dlt_pipeline, load, snapshot
from fdb_scraper.schema import EXPORT_FIELDS, PUBLISHED_FIELDS, USEABLE_FIELDS
from fdb_scraper.scraper import export, scrape

pytestmark = pytest.mark.network

# The published dataset is ~2500 programmes. Bounds rather than a number: the export
# gains and loses programmes weekly, and a test that needed updating every week
# would get its expectations edited rather than its failure investigated. These
# catch a truncated download or a parser that silently stopped finding documents.
MIN_PROGRAMMES = 2000
MAX_PROGRAMMES = 4000


@pytest.fixture(scope="module")
def downloaded():
    """One real download, shared by every test here."""
    with export() as root:
        yield root


def test_the_export_still_has_the_recorded_structure(downloaded) -> None:
    """``check_export`` against today's export, not a fixture of last month's.

    This is the assertion the fixture cannot make. ``generated/contract_data.py``
    records what
    the export looked like when it was generated; only a real download can say
    whether that is still true.
    """
    check_export(downloaded)


def test_the_download_parses_into_the_expected_shape(downloaded) -> None:
    raw = scrape(USEABLE_FIELDS, export_dir=downloaded)
    assert MIN_PROGRAMMES <= raw.height <= MAX_PROGRAMMES, (
        f"{raw.height} programmes parsed, which is outside the plausible range"
    )
    assert list(raw.columns) == list(USEABLE_FIELDS)


def test_collect_validates_against_the_real_export(downloaded) -> None:
    """The closed vocabularies and per-column checks, against real values.

    The fixture has three programmes, so it exercises the schema but cannot show
    that a new upstream category has appeared. This can, and it is the same check
    that will stop a weekly run.
    """
    df = collect(export_dir=downloaded)
    assert list(df.columns) == list(EXPORT_FIELDS)
    assert MIN_PROGRAMMES <= df.height <= MAX_PROGRAMMES


def test_a_load_of_the_real_export_publishes(downloaded, tmp_path, monkeypatch) -> None:
    """Ingest and publish end to end on real data, into a throwaway database.

    Deliberately not the deployment's database: this must not add a version to the
    real history, whose timestamps then claim a change happened when a test ran.
    """
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt"))
    monkeypatch.delenv("POSTGRES_CONN_STR", raising=False)
    pipe = dlt_pipeline(tmp_path / "fdb.duckdb")

    result = load(downloaded, pipe=pipe)
    assert MIN_PROGRAMMES <= result["programmes_after"] <= MAX_PROGRAMMES

    from fdb_scraper.pipeline import publish

    df = publish(pipe=pipe)
    assert list(df.columns) == list(PUBLISHED_FIELDS)
    assert not df["deleted"].any(), "a first load cannot have deleted anything"
    # Every programme is first seen in this load, so none has changed yet.
    assert df["last_updated"].null_count() == df.height

    raw, documents = snapshot(pipe)
    assert documents, "no linked documents were indexed"
