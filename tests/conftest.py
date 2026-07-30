from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from fdb_scraper import collect, resolve, scrape

EXPORT = Path(__file__).parent / "fixtures" / "export"
CONTENT = EXPORT / "BMWI/FDB/Content/DE"

PROGRAMME = CONTENT / "Foerderprogramm/Bund/BA/eingliederungszuschuss-bund.xml"
# Ships a raw \x02 byte mid-word, which XML 1.0 forbids.
MALFORMED = CONTENT / "ExternerLink/R/rgl-buergschaften-als-de-minimis-beihilfe-863736.xml"


def property_named(root: ET.Element, name: str) -> ET.Element:
    return next(p for p in root.findall("property") if p.get("name") == name)

# Programmes in the fixture, keyed by the edge case each one covers.
EINGLIEDERUNG = "bund-ba-eingliederungszuschuss-bund"  # contact with address + website
FILM = "bund-bkm-deutsch-franzoesische-kooperationen-film-ffa"  # 'bundesweit' alias
EINSTIEGSGELD = "bund-bmas-einstiegsgeld"  # two contacts


@pytest.fixture(scope="session")
def export_dir() -> Path:
    return EXPORT


@pytest.fixture(scope="session")
def raw(export_dir):
    return scrape(export_dir=export_dir)


@pytest.fixture(scope="session")
def docs(export_dir):
    return resolve(export_dir)


@pytest.fixture(scope="session")
def df(export_dir):
    return collect(export_dir=export_dir)


@pytest.fixture
def row(df):
    """Look a published row up by its id_url."""

    def _row(id_url: str) -> dict:
        return df.filter(df["id_url"] == id_url).row(0, named=True)

    return _row

