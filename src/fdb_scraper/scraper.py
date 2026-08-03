"""Fetch the Förderdatenbank programme export and parse it into a DataFrame.

The only module that touches the network. Reading the XML is
:mod:`fdb_scraper.parser`, which needs nothing but files on disk -- so anyone who
already has an extracted export can parse it without this module, and a parse can
be rerun against a saved input.

:func:`export` yields the root of an extracted export, downloading one only when
no directory is given, so callers that need more than the programme documents --
the contact and link trees, say -- share a single download.
"""

from __future__ import annotations

import tempfile
import zipfile
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from pathlib import Path

import polars as pl
import requests

from fdb_scraper.config import EXPORT_URL
from fdb_scraper.parser import check_fields, parse_programmes


def _download_and_extract(dest: Path) -> Path:
    response = requests.get(EXPORT_URL, timeout=300)
    response.raise_for_status()
    zip_path = dest / "foerderprogramme_export.zip"
    zip_path.write_bytes(response.content)
    extract_dir = dest / "foerderprogramme_export"
    with zipfile.ZipFile(zip_path) as zip_ref:
        zip_ref.extractall(extract_dir)
    return extract_dir


@contextmanager
def export(export_dir: str | Path | None = None) -> Generator[Path]:
    """Yield the root of an extracted export.

    Downloads into a temporary directory unless ``export_dir`` names one that is
    already extracted. Callers that need more than the programme files -- the
    contact and link trees, say -- share one download this way.
    """
    if export_dir is not None:
        yield Path(export_dir)
        return
    with tempfile.TemporaryDirectory() as tmp:
        yield _download_and_extract(Path(tmp))


def scrape(
    fields: Iterable[str] | None = None,
    *,
    export_dir: str | Path | None = None,
) -> pl.DataFrame:
    """Return one row per Förderprogramm in the Förderdatenbank export.

    Download plus :func:`fdb_scraper.parser.parse_programmes`, nothing else. Call
    that directly to parse an export you already have.

    Args:
        fields: Column names to extract; defaults to all of
            :data:`fdb_scraper.parser.ALL_FIELDS`.
        export_dir: Directory of an already extracted export. When omitted the
            export is downloaded into a temporary directory.
    """
    # Checked before the download, not after: a typo should not cost 50 MB.
    selected = check_fields(fields)
    with export(export_dir) as root:
        return parse_programmes(root, selected)
