"""One weekly run: record the export, then publish what is recorded.

Two steps that are deliberately separable, because they fail for different reasons
and only one of them touches the network:

``ingest``
    Download the export and load it into the scd2 history
    (:func:`fdb_scraper.history.load`). Fails on a structural change upstream, a
    schema-contract violation, a duplicate key or a suspiciously shrunken export --
    in every case *before* anything is written, so last week's files keep serving.

``publish``
    Read the history, process it, validate it, and write the distribution and the
    metadata into a staging tree (:func:`fdb_scraper.pipeline.publish`). Touches no
    network and is a pure function of what was loaded, so it can be rerun after a
    processing fix without refetching.

Usage::

    uv run python scripts/build_dist.py                  # both steps
    uv run python scripts/build_dist.py --no-ingest      # republish what is stored
    uv run python scripts/build_dist.py --out dist       # staging tree, default dist/

The staging tree mirrors the served URL paths, so publishing it is a copy::

    dist/data/programme.csv     ->  /data/programme.csv
    dist/def/fdb.ttl                ->  /def/fdb
    dist/id/dataset/<id>.ttl        ->  /id/dataset/<id>
    dist/table-schema.json          ->  /table-schema.json

The metadata is regenerated here rather than copied from ``dcat/``: the published
copy carries ``dcat:byteSize`` for the file actually written, which the committed
copy deliberately does not. ``dcat/`` stays the reviewable artefact.
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent

CSV_FILE = "programme.csv"
# Published beside the data, not just kept in the repo: a consumer who downloads the
# CSV from the served tree should be able to reach the licence from there. The
# distribution's dct:license points at the licence itself; this states what was done
# to the data and why ND permits it.
LICENCE = "LICENSE_DATA"


def build(
    out: Path,
    *,
    ingest: bool = True,
    db: Path | None = None,
    export_dir: Path | None = None,
) -> Path:
    """Run the pipeline into ``out`` and return the written CSV's path."""
    # Imported late so --help costs nothing and so an ingest-free rerun does not
    # pay for dlt.
    from fdb_scraper.history import load
    from fdb_scraper.pipeline import publish

    if ingest:
        result = load(export_dir)
        print(
            f"ingest: {result['programmes_before']} -> "
            f"{result['programmes_after']} live programmes "
            f"(load {result['load_id']})"
        )
        segmented = result["keywords_segmented"]
        print(
            "segment: skipped, FDB_TAGGER_URL/FDB_TAGGER_TOKEN unset"
            if segmented is None
            else f"segment: {segmented} new keywords values sent to the tagger"
        )
    else:
        print("ingest: skipped, publishing what is already stored")

    df = publish(db=db)
    live = df.height - df["absent"].sum()
    print(f"publish: {df.height} programmes ({live} live), {df.width} columns, validated")
    # The inferred column's coverage, printed rather than checked: a value the
    # segmenter has not seen publishes as null, which is legitimate but worth seeing.
    if "keywords_extracted" in df.columns:
        with_keywords = df["keywords"].is_not_null().sum()
        extracted = (df["keywords_extracted"].list.len().fill_null(0) > 0).sum()
        print(
            f"inferred: keywords_extracted on {extracted} of {with_keywords} rows "
            "that have keywords"
        )

    # Nested columns (lists, structs) cannot be written to CSV directly; pre-encode
    # them as JSON strings in their cell. The convention is published in the DCAT
    # table-schema so consumers know which columns to parse.
    from fdb_scraper.csv_export import flatten_nested

    csv_df = flatten_nested(df)

    data_dir = out / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    # Written to a neighbouring name and moved into place, so a reader never sees a
    # half-written file even if the staging tree is served directly.
    staged = data_dir / f".{CSV_FILE}.incoming"
    csv_df.write_csv(staged)
    csv_path = data_dir / CSV_FILE
    staged.replace(csv_path)
    print(f"{csv_path}: {csv_path.stat().st_size / 1e6:.1f} MB")

    shutil.copyfile(ROOT / LICENCE, out / LICENCE)

    return csv_path


def generate_metadata(out: Path) -> None:
    """Regenerate the DCAT artefacts into ``out``, with byteSize for what was built.

    Then check the written CSV against the schema just generated for it. A publish
    that disagrees with its own metadata about which columns exist is exactly the
    failure a consumer cannot work around, so it fails the run rather than warning.
    """
    # Late like the pipeline imports above, for the same reason.
    from fdb_scraper.dcat import check_csv, distribution_sizes, write_artefacts

    modified = datetime.now(timezone.utc)
    sizes, missing = distribution_sizes(out / "data")
    for note in missing:
        print(note)
    for line in write_artefacts(out, modified, sizes):
        print(line)

    if problems := check_csv(out / "data" / CSV_FILE):
        for problem in problems:
            print(f"csv: {problem}")
        raise SystemExit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "dist",
        help="Staging tree to write. Default: dist/",
    )
    ap.add_argument(
        "--no-ingest",
        action="store_true",
        help="Do not download or load; republish what the history already holds.",
    )
    ap.add_argument(
        "--db",
        type=Path,
        help="DuckDB file holding the history. FDB_DB or the default otherwise.",
    )
    ap.add_argument(
        "--export-dir",
        type=Path,
        help="An already extracted export to load instead of downloading one. What "
        "the container smoke test points at the test fixture, so it exercises this "
        "exact command without fetching 28 MB.",
    )
    args = ap.parse_args()

    build(
        args.out,
        ingest=not args.no_ingest,
        db=args.db,
        export_dir=args.export_dir,
    )
    generate_metadata(args.out)
    print(f"\n{args.out} is ready to publish")


if __name__ == "__main__":
    raise SystemExit(main())
