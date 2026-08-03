"""Land each export run in a database, keeping the history of what changed.

:mod:`fdb_scraper.pipeline` answers "what does the export say today". This module
answers "what did it say last month, and when did this programme appear" -- which
the export cannot, because it only ever ships the current state.

Two tables, both loaded with dlt's ``scd2`` merge strategy:

``programmes``
    One row per Förderprogramm per *version*, holding the raw export fields --
    values before :func:`fdb_scraper.process.decode` touches them, classifier
    hrefs intact. Raw on purpose: a parse bug found next month can be re-run
    against the exact input it applied to, which a table of decoded values cannot
    support. This is the export retention the README lists as planned.

``documents``
    The linked-document index :func:`fdb_scraper.links.resolve` builds, one row
    per document. Needed alongside the raw programmes because
    :func:`fdb_scraper.process.process` reads both -- storing only the programmes
    would make the retention claim half true.

Both resources are full extracts, so no ``merge_key`` is set: dlt then treats a
record absent from a load as deleted and closes its validity window. That is how
``on_website_to`` comes to mean "the run in which the programme left the export",
with no delete detection of our own.

A third table is written here but is not part of that arrangement:

``keyword_segments``
    One row per distinct ``keywords`` string, holding the keywords a model read out
    of it (services/keyword_segmenter). Content-addressed on ``md5(keywords)`` and
    merged rather than scd2'd, because a segmentation is a function of the string
    and not a state upstream reports -- there is no version of it to keep. It is
    written here, and not in the publish step, for the reason the raw export is
    stored at all: model inference is not bit-reproducible, so the published column
    has to be read from a materialised result rather than recomputed. See
    :func:`segment_keywords`.

``id_hash`` is minted before the load, by the same
:func:`fdb_scraper.process.add_identifiers` the published table uses. Not because
dlt needs it -- scd2 matches versions by row hash -- but because
:func:`snapshot` groups a programme's versions by it to derive first-seen, last
changed and whether it is still there.




Usage::

    uv run python -m fdb_scraper.history                    # download, then load
    uv run python -m fdb_scraper.history --export-dir data/foerderprogramme_export

The database path comes from ``FDB_DB``, defaulting to :data:`DEFAULT_DB`.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import dlt
import polars as pl

from fdb_scraper.contract import check_export
from fdb_scraper.links import DOC_FIELDS, resolve
from fdb_scraper.parser import EXPORT_SCHEMA
from fdb_scraper.process import add_identifiers
from fdb_scraper.schema import HISTORY_COLUMNS, TIMESTAMP, USEABLE_FIELDS
from fdb_scraper.scraper import export, scrape

PIPELINE_NAME = "fdb"
DATASET_NAME = "foerderdatenbank"
# Beside the built distributions rather than in the repo: it is state, not source.
DEFAULT_DB = Path("data") / "fdb.duckdb"

# Named rather than left as dlt's _dlt_valid_from/_dlt_valid_to: these are
# published semantics, carried over from the funding_crawler dataset so a
# consumer of the previous publication reads the same two columns.
VALIDITY_COLUMNS = ["on_website_from", "on_website_to"]

SCD2 = {
    "disposition": "merge",
    "strategy": "scd2",
    "validity_column_names": VALIDITY_COLUMNS,
    # active_record_timestamp is left at its default of NULL rather than a high
    # sentinel like 9999-12-31: `fold` asks "is the window open" as IS NULL, and a
    # sentinel would put a fictional date in the history instead.
    #
    # row_version_column_name is left unset too, so dlt hashes every column. A
    # supplied hash would mean choosing which fields count as a change, and for a
    # table whose job is to record what the export said, all of them do. Nothing in
    # the raw frame is volatile -- no scrape timestamp, no run id -- so hashing
    # everything cannot manufacture churn. ``funding_crawler`` computed its own
    # checksum because it scraped HTML, where much of the page is noise.
}

# dlt's default is to evolve the schema silently. That is the opposite of how this
# repo treats upstream drift everywhere else: `contract.check_export` raises rather
# than let a renamed property go null, and the pandera schema raises rather than
# publish an unknown category. A new or retyped column here means the export
# changed under us, so it should stop the run rather than appear in the history as
# a column that exists for some rows and not others.
#
# Tables stay on evolve because the contract is checked before the first load too,
# and freezing them means the pipeline cannot create its own tables. Only this
# source yields into the dataset, so there is no rogue table to guard against --
# whereas the columns are generated from EXPORT_SCHEMA, which makes "a column that
# was not declared" exactly the signal worth failing on.
SCHEMA_CONTRACT = {"tables": "evolve", "columns": "freeze", "data_type": "freeze"}

# A run that loses this fraction of the current programmes is treated as a broken
# export rather than a real deletion. Chosen loose: the export has swung by tens
# of programmes between weeks, and the point is to catch a truncated download,
# not to police churn.
MAX_SHRINK = 0.2
# Below this many live programmes the fraction says nothing -- losing one of three
# is 33% and entirely normal. The real export holds ~2500, so the guard is active
# in every run that matters and quiet on fixtures.
MIN_GUARDED = 100

# Polars dtype -> dlt data type, for generating column hints from the export
# schema. Lists become JSON: the 31 category columns hold values of a programme,
# not entities of their own, so normalising them into child tables would give
# each category value its own scd2 history.
_DLT_TYPES = {pl.String: "text", pl.Int64: "bigint"}


def _dlt_type(dtype) -> str:
    if isinstance(dtype, pl.List) or dtype is pl.List:
        return "json"
    if isinstance(dtype, pl.Datetime) or dtype is pl.Datetime:
        return "timestamp"
    return _DLT_TYPES[dtype]


def _hints(schema: dict) -> dict[str, dict]:
    """Column hints for every field, whether or not a load carries a value.

    dlt infers types from the data it sees and silently omits a column that was
    null throughout a load. For a table whose job is to be a faithful record of
    the export that is a bug: the stored shape would depend on the run, and a
    field that happened to be empty one week would vanish from the history. The
    hints are generated from :data:`fdb_scraper.parser.EXPORT_SCHEMA`, so a new
    export field is covered without an edit here.
    """
    return {name: {"data_type": _dlt_type(dtype)} for name, dtype in schema.items()}


PROGRAMME_HINTS = _hints({f: EXPORT_SCHEMA[f] for f in USEABLE_FIELDS}) | {
    "id_url": {"data_type": "text"},
    "id_hash": {"data_type": "text"},
}
# resolve() returns flat strings throughout, plus the two it derives itself.
DOCUMENT_HINTS = {
    name: {"data_type": "text"}
    for name in ("href", *DOC_FIELDS.values(), "website")
}


def _rows(df: pl.DataFrame) -> Iterator[dict]:
    """One dict per row, with datetimes left as datetimes for dlt to type."""
    yield from df.iter_rows(named=True)


def _assert_unique(df: pl.DataFrame, key: str) -> pl.DataFrame:
    """Refuse to load a frame with a repeated key.

    scd2 does not deduplicate within a load, and ``primary_key`` does not make it
    -- a key yielded twice lands as two rows with open validity windows, and every
    later run keeps both alive. That is the state ``funding_crawler``'s
    ``queries/fix_dupliates.sql`` exists to repair by hand. Cheaper to refuse the
    load: a duplicate here means the export contains one, which is a finding.
    """
    duplicated = df.filter(pl.col(key).is_duplicated())
    if not duplicated.is_empty():
        keys = sorted(set(duplicated[key].to_list()))
        raise ValueError(f"{len(keys)} duplicate {key} in this export: {keys[:5]}")
    return df


@dlt.resource(
    name="programmes",
    write_disposition=SCD2,
    columns=PROGRAMME_HINTS,
    # Not the scd2 natural key -- that is merge_key, left unset so a record absent
    # from a full extract is retired. Declared so the key is visible in the
    # schema; it neither matches versions nor deduplicates, which _assert_unique
    # does instead.
    primary_key="id_hash",
    # Belt and braces with the json hints above: nesting is what turns a list
    # column into a child table, and neither alone is documented to stop it.
    max_table_nesting=0,
)
def programmes(export_dir: str | Path) -> Iterator[dict]:
    """Raw export rows, keyed by the identifiers derived from the URL.

    ``add_identifiers`` is reused rather than reimplemented: it derives
    ``id_url`` and ``id_hash`` from ``url`` alone, so it is as applicable to a raw
    frame as to a decoded one and the key here is the same one the published
    table carries.
    """
    raw = scrape(USEABLE_FIELDS, export_dir=export_dir)
    yield from _rows(_assert_unique(add_identifiers(raw), "id_hash"))


@dlt.resource(
    name="documents",
    write_disposition=SCD2,
    columns=DOCUMENT_HINTS,
    primary_key="href",
)
def documents(export_dir: str | Path) -> Iterator[dict]:
    """The linked-document index, one row per document, keyed by its href.

    ``resolve`` returns a dict keyed by href, so duplicates cannot survive it --
    no uniqueness guard needed here, unlike the programmes.
    """
    for href, doc in resolve(export_dir).items():
        yield {"href": href, **doc}


@dlt.source(name="foerderdatenbank", schema_contract=SCHEMA_CONTRACT)
def export_source(export_dir: str | Path):
    return [programmes(export_dir), documents(export_dir)]


# --- The inferred column's input ---------------------------------------------
KEYWORD_TABLE = "keyword_segments"
# The segmenter lives beside its Modal app, its training data and its evaluation
# rather than in the installed package: the pipeline needs 60 lines of HTTP client
# from it, and the rest is 437 MB of weights, torch and a notebook's worth of
# measurement the pipeline host must not have to install. Imported by path for the
# same reason tests/test_keyword_segmenter.py does.
SEGMENTER_DIR = Path(
    os.environ.get("FDB_SEGMENTER_DIR")
    or Path(__file__).resolve().parents[2] / "services" / "keyword_segmenter"
)


def _segment_client_module():
    """``segment.client``, with ``services/keyword_segmenter`` put on the path."""
    if str(SEGMENTER_DIR) not in sys.path:
        sys.path.insert(0, str(SEGMENTER_DIR))
    from segment import client  # noqa: PLC0415 -- imported by path, see above

    return client


def tagger_configured() -> bool:
    """Whether the endpoint's URL and token are both in the environment.

    A run without them loads the export and skips the segmentation, rather than
    failing: recording what upstream said is the load's job and must not depend on
    a model service being up. The column then publishes as null for whatever has no
    materialised segmentation, which is what ``keywords_extracted`` being nullable
    is for.
    """
    return bool(os.environ.get("FDB_TAGGER_URL") and os.environ.get("FDB_TAGGER_TOKEN"))


@dlt.resource(
    name=KEYWORD_TABLE,
    # Upsert on the content hash, not scd2. The row says "this string segments like
    # this", which has no history: a changed keywords value is a different string
    # and therefore a different row, and re-running a newer model over an old string
    # replaces the answer rather than adding a version of it.
    write_disposition="merge",
    primary_key="md5",
    columns={
        "md5": {"data_type": "text"},
        "keywords": {"data_type": "text"},
        "terms": {"data_type": "json"},
        # Which checkpoint produced the row, so a re-segmentation can be told from
        # a stale one when the model is retrained.
        "model": {"data_type": "text"},
        # What produced it, precisely: code, lexicon and checkpoint together. A row
        # whose revision is not the one the endpoint now serves is re-sent by
        # :func:`segment_keywords`, so improving the segmenter reaches the published
        # column without anyone remembering to clear this table. Nullable, because
        # rows written before the revision existed have none.
        "revision": {"data_type": "text", "nullable": True},
    },
    max_table_nesting=0,
)
def keyword_segments(rows: list[dict]) -> Iterator[dict]:
    yield from rows


def stored_keywords(pipe) -> list[str]:
    """Every distinct non-null ``keywords`` value in the history.

    Every version, not only the live ones: a programme that has left the export is
    still published, carrying its last known values, so its keywords need a
    segmentation too.
    """
    with pipe.sql_client() as client:
        with client.execute_query(
            f"SELECT DISTINCT keywords "
            f"FROM {client.make_qualified_table_name('programmes')} "
            f"WHERE keywords IS NOT NULL"
        ) as cursor:
            return [row[0] for row in cursor.fetchall()]


def segments(pipe=None) -> dict[str, list[str]]:
    """Materialised segmentations as ``keywords`` -> its keywords.

    Empty before the segmenter has ever run, which is not an error: the published
    column is nullable precisely so a dataset can be published without it.
    """
    pipe = pipe or dlt_pipeline()
    try:
        with pipe.sql_client() as client:
            with client.execute_query(
                f"SELECT keywords, terms "
                f"FROM {client.make_qualified_table_name(KEYWORD_TABLE)}"
            ) as cursor:
                rows = cursor.fetchall()
    except Exception:
        # The table does not exist yet. Same reasoning as count_live: a real
        # failure surfaces on the query the caller actually depends on.
        return {}
    # Stored as JSON, so it comes back as text on both destinations.
    return {
        keywords: (json.loads(terms) if isinstance(terms, str) else terms)
        for keywords, terms in rows
    }


def stored_revisions(pipe=None) -> dict[str, str | None]:
    """``keywords`` -> the segmenter revision that produced its stored terms.

    ``None`` for rows written before revisions were recorded, which makes them stale
    by definition -- exactly right, since those are the rows produced by the code
    that got the agency names wrong.
    """
    pipe = pipe or dlt_pipeline()
    try:
        with pipe.sql_client() as client:
            with client.execute_query(
                f"SELECT keywords, revision "
                f"FROM {client.make_qualified_table_name(KEYWORD_TABLE)}"
            ) as cursor:
                return dict(cursor.fetchall())
    except Exception:
        # The table, or the column, does not exist yet. Same reasoning as segments().
        return {}


def segment_keywords(pipe=None, *, client=None) -> dict:
    """Segment every stored ``keywords`` value whose segmentation is missing or stale.

    Idempotent and cheap to repeat: a value already segmented **by the revision the
    endpoint currently serves** is skipped here, and the client's own sqlite cache
    dedupes again beneath that, so a rerun after a fixed export costs one query and
    one round trip. Only genuinely new strings reach the endpoint -- a weekly export
    brings a handful.

    Staleness is checked rather than assumed because the alternative failed in
    practice. When function-word glue was added to the decoder, every one of the 2340
    stored rows was still keyed only on its string, so the improved segmentation
    reached nothing: the table answered "already done" and the endpoint was never
    asked. A revision mismatch now re-sends the row, which means shipping a better
    segmenter is a deploy, not a deploy plus a remembered ``DELETE``.

    The cost is one request per run even when nothing has changed -- the revision is
    the endpoint's to report, since the checkpoint lives on a Volume the pipeline
    never reads. A wrong published column is worth more than that request.

    Args:
        pipe: An existing pipeline. Built from the environment otherwise.
        client: A ``TaggerClient``, for tests. Built from ``FDB_TAGGER_URL`` and
            ``FDB_TAGGER_TOKEN`` otherwise.

    Returns:
        ``values`` distinct strings in the history, ``segmented`` sent to the
        endpoint, ``resegmented`` of those that were already stored under an older
        revision, ``stored`` in the table afterwards.
    """
    pipe = pipe or dlt_pipeline()
    values = stored_keywords(pipe)
    known = stored_revisions(pipe)

    segment_client = _segment_client_module()
    owned = client is None
    if client is None:
        # The client's own sqlite cache, which the deployment has to place on the
        # writable state volume: the image's working directory is not writable by
        # the user the pipeline runs as, and the client's default is relative to it.
        cache_path = os.environ.get("FDB_KEYWORD_CACHE")
        client = segment_client.TaggerClient(
            cache=segment_client.Cache(cache_path) if cache_path else None
        )
    try:
        revision = client.revision()
        missing = [v for v in values if known.get(v) != revision]
        stale = sum(1 for v in missing if v in known)
        results = client.segment(missing) if missing else []
    finally:
        if owned:
            client.close()

    if not missing:
        return {
            "values": len(values),
            "segmented": 0,
            "resegmented": 0,
            "stored": len(known),
        }

    pipe.run(
        keyword_segments([
            {
                # Keyed on the string alone, so a new revision replaces the answer
                # instead of adding a second row for the same value.
                "md5": segment_client.fingerprint(keywords),
                "keywords": keywords,
                "terms": result["terms"],
                "model": result.get("model"),
                "revision": revision,
            }
            for keywords, result in zip(missing, results)
        ])
    )
    return {
        "values": len(values),
        "segmented": len(missing),
        "resegmented": stale,
        "stored": len(known) + len(missing) - stale,
    }


def dlt_pipeline(db: str | Path | None = None, *, conn_str: str | None = None):
    """The dlt pipeline, on Postgres in production and a DuckDB file otherwise.

    Postgres wins for the deployed pipeline on one argument: the history is the
    only artefact here that cannot be rebuilt, because upstream serves only
    today's export -- and Coolify backs up Postgres on a schedule with offsite
    copies, while it does not back up files or volumes at all. ``pg_dump`` is also
    consistent against a live database, where copying a DuckDB file that is being
    written is not. Continuous readers are the other reason: DuckDB takes an
    exclusive write lock, so a BI tool holding the file open would fail the load.

    DuckDB stays supported and is the default, because it needs no service: it is
    what the tests and any local run use. scd2 is generated per destination, so
    ``tests/test_history.py`` runs against both rather than assuming they agree.

    Args:
        db: Path to a DuckDB file. Used when no connection string is available.
        conn_str: Postgres connection string. ``POSTGRES_CONN_STR`` otherwise.
    """
    conn_str = conn_str or os.environ.get("POSTGRES_CONN_STR")
    if conn_str and db is None:
        destination = dlt.destinations.postgres(conn_str)
    else:
        path = Path(db or os.environ.get("FDB_DB") or DEFAULT_DB)
        path.parent.mkdir(parents=True, exist_ok=True)
        destination = dlt.destinations.duckdb(str(path))
    return dlt.pipeline(
        pipeline_name=PIPELINE_NAME,
        destination=destination,
        dataset_name=DATASET_NAME,
    )


def load(
    export_dir: str | Path | None = None,
    *,
    pipe=None,
    check_contract: bool = True,
    segment: bool = True,
) -> dict:
    """Load one export run and return what changed.

    Args:
        export_dir: An already extracted export. Downloaded into a temporary
            directory when omitted.
        pipe: An existing pipeline, for tests. Built from the environment
            otherwise.
        check_contract: Check the export's structure before reading it. On by
            default and more important here than in :func:`fdb_scraper.collect`:
            a renamed property parses as null, and a null written into the history
            stays there. ``collect`` can be rerun once the parser is fixed; a load
            cannot, because upstream no longer serves the export it read.
        segment: Segment the new ``keywords`` values after the load. On by default
            but a no-op unless the endpoint is configured -- see
            :func:`tagger_configured`. Deliberately after the load and not part of
            ``export_source``: the export has to be recorded whether or not a model
            service is reachable, and the strings to segment are the stored ones.

    Raises:
        ContractError: If the export's structure is not as recorded.
        RuntimeError: If the load package is empty, or if the run would retire more
            than :data:`MAX_SHRINK` of the live programmes. A truncated or
            half-written export looks exactly like a mass deletion, and scd2 would
            close those validity windows for good, so the load is rejected instead.
    """
    pipe = pipe or dlt_pipeline()
    before = count_live(pipe, "programmes")

    with export(export_dir) as root:
        if check_contract:
            # Once, before either resource reads: both walk the same tree, and the
            # check has to happen before anything is written rather than per row.
            check_export(root)
        info = pipe.run(export_source(root))

    # dlt raises on a failed job by default, but a package can also complete with
    # none: an empty extract loads cleanly and would silently retire everything.
    loaded = {
        name: metrics
        for package in info.load_packages
        for name, metrics in package.jobs.items()
    }
    if not loaded:
        raise RuntimeError("the load package was empty; nothing was extracted")

    after = count_live(pipe, "programmes")
    if before >= MIN_GUARDED and after < before * (1 - MAX_SHRINK):
        raise RuntimeError(
            f"run retired {before - after} of {before} programmes "
            f"({1 - after / before:.0%}); refusing a load that looks truncated"
        )
    result = {
        "programmes_before": before,
        "programmes_after": after,
        "load_id": info.loads_ids[0] if info.loads_ids else None,
        "keywords_segmented": None,
    }
    # After the shrink guard: a rejected load must not spend money on a model call,
    # and the strings it would segment are ones the guard just refused to trust.
    if segment and tagger_configured():
        result["keywords_segmented"] = segment_keywords(pipe)["segmented"]
    return result


def count_live(pipe, table: str) -> int:
    """Rows of ``table`` whose validity window is still open, 0 before first load."""
    to_column = VALIDITY_COLUMNS[1]
    try:
        with pipe.sql_client() as client:
            with client.execute_query(
                f"SELECT COUNT(*) FROM {client.make_qualified_table_name(table)} "
                f"WHERE {to_column} IS NULL"
            ) as cursor:
                return cursor.fetchone()[0]
    except Exception:
        # First run: the dataset does not exist yet. Any other failure surfaces
        # on the load itself, which happens either way.
        return 0


# HISTORY_COLUMNS lives in schema.py, where its pandera entries are: :func:`fold`
# derives the values and the schema states what they have to look like, and the two
# drifting apart is exactly the failure to prevent.

# Stored as JSON, so they come back as JSON text and have to be decoded before
# anything downstream treats them as lists again.
_LIST_FIELDS = tuple(
    f for f in USEABLE_FIELDS if isinstance(EXPORT_SCHEMA[f], pl.List)
)


def snapshot(pipe=None) -> tuple[pl.DataFrame, dict[str, dict]]:
    """Every programme ever seen, with its history folded in.

    Not a live-rows filter. A programme that has left the export stays, carrying
    the content of its last version and ``deleted = True`` -- the same shape
    ``funding_crawler``'s ``gen_query`` published, so the dataset outlives the
    export rather than forgetting whatever upstream removed.

    Reads what the last load recorded rather than downloading, so a publish can be
    rerun against the same input. That is the point of storing the raw fields.
    """
    pipe = pipe or dlt_pipeline()
    with pipe.sql_client() as client:
        versions = pl.read_database(
            f"SELECT * FROM {client.make_qualified_table_name('programmes')}",
            connection=client.native_connection,
            infer_schema_length=None,
        )
        docs = pl.read_database(
            f"SELECT * FROM {client.make_qualified_table_name('documents')} "
            f"WHERE {VALIDITY_COLUMNS[1]} IS NULL",
            connection=client.native_connection,
            infer_schema_length=None,
        )
    return fold(versions), _index(docs)


def fold(versions: pl.DataFrame) -> pl.DataFrame:
    """One row per ``id_hash`` from all of its versions, plus the history columns.

    Content comes from the live version where there is one and from the most
    recently retired version otherwise, which is what makes a deleted programme
    keep its last known values.

    ``on_website_from`` is the *minimum* over every version -- when the programme
    first appeared. scd2 restarts the validity window on each change, so the live
    row's own value says only when the current version appeared;
    ``funding_crawler`` reached one retirement back for this and so understated
    the age of anything that changed more than once.

    Pure, and separate from :func:`snapshot`, so the derivation is testable
    without a database.
    """
    frm, to = VALIDITY_COLUMNS
    history = (
        versions.group_by("id_hash")
        .agg(
            pl.col(frm).min().alias("on_website_from"),
            pl.col(to).max().alias("last_updated"),
            pl.col(to).drop_nulls().sort().alias("previous_update_dates"),
            # A programme is gone when no version of it has an open window.
            pl.col(to).is_null().any().not_().alias("deleted"),
        )
        # Typed after aggregating, not inside it. On a first load nothing has been
        # retired, so the max is null throughout and every list is empty, which
        # polars types Null and List(Null) -- neither is what the schema declares.
        # Casting inside agg would instead cast the element expression, wrapping
        # each timestamp in a list of its own.
        .with_columns(
            pl.col("on_website_from").cast(TIMESTAMP),
            pl.col("last_updated").cast(TIMESTAMP),
            pl.col("previous_update_dates").cast(pl.List(TIMESTAMP)),
        )
    )
    # nulls_last puts the live version at the end of its group, and the retired
    # ones in retirement order, so the last row per group is the live version if
    # there is one and the most recently retired otherwise.
    latest = (
        versions.sort(to, nulls_last=True)
        .group_by("id_hash", maintain_order=True)
        .agg(pl.all().last())
        # The per-version window is dropped before the join, not after: it shares
        # a name with the aggregate above, and a collision would silently leave
        # the latest version's from-date in place of the minimum.
        .drop(VALIDITY_COLUMNS)
    )
    return _restore(latest.join(history, on="id_hash", how="left"))


def _dlt_columns(df: pl.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith("_dlt_")] + VALIDITY_COLUMNS


def _restore(raw: pl.DataFrame) -> pl.DataFrame:
    """A folded frame in the shape :func:`fdb_scraper.process.process` expects.

    Drops dlt's bookkeeping and the per-version validity columns, whose meaning
    the history columns now carry, and puts the export fields back in
    ``USEABLE_FIELDS`` order -- a database returns whatever order it likes, and
    the published column order is a promise. ``id_url`` and ``id_hash`` are
    dropped too: ``process`` mints them from ``url``, and keeping the stored ones
    would let a stale value disagree with the derivation.
    """
    keep = [c for c in USEABLE_FIELDS if c in raw.columns]
    keep += [c for c in HISTORY_COLUMNS if c in raw.columns]
    return _retype(raw.select(keep))


def _retype(df: pl.DataFrame) -> pl.DataFrame:
    """Coerce the stored export fields back to the dtypes ``scrape`` produced.

    The round trip is not symmetric in two ways. The list columns go in as lists,
    are stored as JSON and come back as text. And a column that was null for every
    row of every load comes back as ``Null``, since there was never a value to read
    a type from -- seven of them in the test fixture.

    Both are repaired against :data:`fdb_scraper.parser.EXPORT_SCHEMA` rather than
    left to inference, so what this module hands over matches
    :func:`fdb_scraper.scrape` whatever a particular load happened to contain.
    Storage stays a detail of this module.
    """
    exprs = []
    for name in df.columns:
        target = EXPORT_SCHEMA.get(name)
        if target is None or df.schema[name] == target:
            continue  # a history column, or already right
        if name in _LIST_FIELDS and df.schema[name] == pl.String:
            exprs.append(pl.col(name).str.json_decode(target).alias(name))
        else:
            exprs.append(pl.col(name).cast(target).alias(name))
    return df.with_columns(exprs) if exprs else df


def _index(docs: pl.DataFrame) -> dict[str, dict]:
    """A stored documents frame back as ``resolve``'s href -> fields mapping."""
    drop = set(_dlt_columns(docs)) | {"href"}
    fields = [c for c in docs.columns if c not in drop]
    return {
        row["href"]: {f: row[f] for f in fields} for row in docs.iter_rows(named=True)
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--export-dir",
        type=Path,
        help="An already extracted export. Downloaded when omitted.",
    )
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = load(args.export_dir)
    print(
        f"{stamp}: {result['programmes_before']} -> {result['programmes_after']} "
        "live programmes"
    )


if __name__ == "__main__":
    main()
