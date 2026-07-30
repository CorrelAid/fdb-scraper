"""Scrape, resolve, process, publish and validate in one call.

Two entry points, because two things are wanted at different times.

:func:`collect` reads one export and returns what it says, with no state
anywhere. That is the parser's contract and what the tests exercise.

:func:`publish` returns what gets published, which is more than any single export
can say: a programme that has left the export is still in it, flagged, and every
programme carries when it was first seen and last changed. Those come from the
scd2 history in :mod:`fdb_scraper.history`, so this path needs the database -- as
does ``keywords_extracted``, whose values are materialised there rather than
recomputed per publish.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import polars as pl

from fdb_scraper.contract import check_export
from fdb_scraper.links import resolve
from fdb_scraper.process import process
from fdb_scraper.schema import (
    EXPORT_FIELDS,
    PUBLISHED_FIELDS,
    USEABLE_FIELDS,
    build_schema,
)
from fdb_scraper.scraper import export, scrape


def collect(
    fields: Iterable[str] | None = None,
    *,
    export_dir: str | Path | None = None,
    validate: bool = True,
    check_contract: bool = True,
) -> pl.DataFrame:
    """Return what one export says, as the published table.

    The history columns are not here: nothing in a single export can supply them.
    Use :func:`publish` for the table as published.

    Args:
        fields: Column names to keep; defaults to all of
            :data:`fdb_scraper.schema.EXPORT_FIELDS`.
        export_dir: Directory of an already extracted export. When omitted the
            export is downloaded into a temporary directory.
        validate: Validate against the pandera schema and raise on any failure.
        check_contract: Check the export's structure before reading it, so a
            renamed or retyped property raises instead of silently going null.
    """
    selected = list(EXPORT_FIELDS) if fields is None else list(fields)
    unknown = set(selected) - set(EXPORT_FIELDS)
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")

    with export(export_dir) as root:
        if check_contract:
            check_export(root)
        # One download serves both the programme files and the linked documents.
        raw = scrape(USEABLE_FIELDS, export_dir=root)
        documents = resolve(root)
        df = process(raw, documents)

    df = df.select(selected)
    if validate:
        # lazy=True so one run reports every violation instead of the first.
        build_schema(selected).validate(df, lazy=True)
    return df


def publish(
    fields: Iterable[str] | None = None,
    *,
    db: str | Path | None = None,
    pipe=None,
    validate: bool = True,
) -> pl.DataFrame:
    """Return the table as published, history included, from what was last loaded.

    Reads the scd2 tables rather than downloading, so this is a pure function of
    what :func:`fdb_scraper.history.load` has recorded. Running it twice publishes
    the same thing, and a publish can be rerun after fixing the processing code
    without refetching -- which is why the raw fields are stored.

    Args:
        fields: Column names to keep; defaults to all of
            :data:`fdb_scraper.schema.PUBLISHED_FIELDS`.
        db: Path to a DuckDB file, when not using Postgres.
        pipe: An existing dlt pipeline, for tests. Built from the environment
            otherwise -- Postgres if ``POSTGRES_CONN_STR`` is set, else DuckDB.
        validate: Validate against the pandera schema and raise on any failure.

    Raises:
        ValueError: If ``fields`` names something that is not published.
    """
    # Imported here, not at module scope: history pulls in dlt, and a plain
    # ``collect`` should not pay for that.
    from fdb_scraper.history import dlt_pipeline, segments, snapshot

    selected = list(PUBLISHED_FIELDS) if fields is None else list(fields)
    unknown = set(selected) - set(PUBLISHED_FIELDS)
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")

    pipe = pipe or dlt_pipeline(db)
    raw, documents = snapshot(pipe)
    # Read, not recomputed: the keyword segmentation is a model's output, so a
    # publish that called the model would not be a pure function of the history.
    # Empty before the segmenter has run, which publishes the column as null
    # throughout rather than failing -- see fdb_scraper.history.segment_keywords.
    df = process(raw, documents, segments(pipe)).select(selected)
    if validate:
        build_schema(selected).validate(df, lazy=True)
    return df
