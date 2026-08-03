"""Regenerate the published DCAT-AP.de metadata and the CSVW table schema.

Everything this writes, and why it is shaped the way it is, is documented in
:mod:`fdb_scraper.dcat` -- this file is the command line around it.

Usage::

    uv run python scripts/gen_dcat.py                        # metadata only
    uv run python scripts/gen_dcat.py --data-dir dist        # + byteSize per file
    uv run python scripts/gen_dcat.py --check-csv dist/data/programme.csv

Artefacts land in ``dcat/`` and are committed, so a change to what is published
shows up as a reviewable diff -- same arrangement as ``scripts/gen_vocab.py`` and
``scripts/gen_contract.py``.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from pathlib import Path

from fdb_scraper.dcat import check_csv, distribution_sizes, write_artefacts

ROOT = Path(__file__).parent.parent
# The committed copy. Its layout mirrors the served URLs, so ``dcat/def/fdb.ttl``
# answers for ``/def/fdb``; see Caddyfile.
OUT_DIR = ROOT / "dcat"


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

    sizes: dict[str, int] = {}
    if args.data_dir:
        sizes, missing = distribution_sizes(args.data_dir)
        for note in missing:
            print(note)

    for line in write_artefacts(args.out_dir or OUT_DIR, modified, sizes):
        print(line)


if __name__ == "__main__":
    main()
