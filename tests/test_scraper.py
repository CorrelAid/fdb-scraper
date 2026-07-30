from xml.etree import ElementTree as ET

import polars as pl
import pytest

from fdb_scraper.scraper import (
    ALL_FIELDS,
    EXPORT_SCHEMA,
    _clean,
    _parse_iso,
    _strip_html,
    parse_xml,
    scrape,
)
from tests.conftest import MALFORMED


def test_malformed_document_is_rejected_by_a_strict_parse():
    """Guards the premise of the next test: this file really is invalid XML."""
    assert b"\x02" in MALFORMED.read_bytes()
    with pytest.raises(ET.ParseError):
        ET.parse(MALFORMED)


def test_parse_xml_repairs_forbidden_control_characters():
    root = parse_xml(MALFORMED)
    title = next(p for p in root.findall("property") if p.get("name") == "gsb:title")
    text = _strip_html(title.find("text").text)
    # The control character is dropped, so the word around it closes up.
    assert "De-minimis-Beihilfen" in text
    assert "\x02" not in text


def test_strip_html_unwraps_cdata_and_collapses_whitespace():
    raw = "&lt;![CDATA[&lt;div&gt;&lt;p&gt;Ein   Satz&lt;/p&gt;\n&lt;p&gt;und noch einer&lt;/p&gt;&lt;/div&gt;]]&gt;"
    assert _strip_html(raw) == "Ein Satz und noch einer"


def test_strip_html_maps_empty_markup_to_none():
    assert _strip_html("&lt;![CDATA[&lt;div&gt;&lt;/div&gt;]]&gt;") is None
    assert _strip_html("") is None
    assert _strip_html(None) is None


def test_parse_iso_discards_unparseable_timestamps():
    """A raw string here would put a str in a Datetime column and fail the frame."""
    assert _parse_iso("2024-08-21T12:15:00+02:00").year == 2024
    assert _parse_iso("nicht datiert") is None
    assert _parse_iso(None) is None


def test_urls_are_built_from_the_export_tree(raw):
    assert sorted(raw["url"]) == [
        "https://www.foerderdatenbank.de/FDB/Content/DE/Foerderprogramm/Bund/BA/eingliederungszuschuss-bund.html",
        "https://www.foerderdatenbank.de/FDB/Content/DE/Foerderprogramm/Bund/BKM/deutsch-franzoesische-kooperationen-film-ffa.html",
        "https://www.foerderdatenbank.de/FDB/Content/DE/Foerderprogramm/Bund/BMAS/einstiegsgeld.html",
    ]


def test_schema_is_declared_not_inferred(raw):
    """Columns and dtypes must not depend on which documents were parsed.

    ``external_id`` is populated on 10 of 2500 programmes and on none of the
    fixture's three, so an inferred schema drops the column outright; list
    columns that are empty throughout come back as List(Null).
    """
    assert raw.columns == list(ALL_FIELDS)
    assert dict(raw.schema) == EXPORT_SCHEMA
    assert raw["external_id"].null_count() == 3
    assert raw.schema["uf_wohnungsbau"] == pl.List(pl.String)


def test_classifier_links_keep_their_full_href(raw):
    """process() strips these to leaf labels; resolution needs the full path."""
    row = raw.filter(pl.col("url").str.ends_with("eingliederungszuschuss-bund.html")).row(
        0, named=True
    )
    assert row["foerderart"] == ["target:/BMWI/SiteGlobals/Categories/FDB/Foerderart/zuschuss"]
    assert row["kontakt"] == [
        "target:/BMWI/FDB/Content/DE/Kontakt/B/bundesagentur-fuer-arbeit-arbeitgeberservice"
    ]


def test_unknown_field_is_rejected(export_dir):
    with pytest.raises(ValueError, match="unknown fields"):
        scrape(["title", "nope"], export_dir=export_dir)


def test_missing_programme_directory_names_the_path(tmp_path):
    """A wrong root must say so, not surface as a missing-column error."""
    with pytest.raises(FileNotFoundError, match="Foerderprogramm"):
        scrape(export_dir=tmp_path)


def test_invisible_characters_are_dropped():
    """SOFT HYPHEN and the C0/C1 controls render as nothing but break matching."""
    assert _clean("Bundes\u00adland") == "Bundesland"
    assert _clean("Zu\u0090schuss") == "Zuschuss"


def test_whitespace_is_collapsed_and_trimmed():
    """NO-BREAK SPACE and TAB are whitespace, so WS_RE handles them."""
    assert _clean("  Aus\u00a0und\tWeiter  bildung ") == "Aus und Weiter bildung"


def test_a_value_field_is_cleaned_like_rich_text(df):
    """keywords comes through _value and used to ship a literal tab."""
    keywords = df["keywords"].drop_nulls().to_list()
    assert keywords
    assert not any("\t" in k or "\u00ad" in k or "  " in k for k in keywords)


def test_text_of_only_invisible_characters_becomes_null():
    assert _clean("\u00ad\u0090 ") is None
