"""Turn the export's XML documents into rows, with no network and no database.

Everything here is a pure function of files on disk: point it at an extracted
export and it returns the same frame every time. That is why it lives apart from
:mod:`fdb_scraper.scraper`, which downloads the zip, and from
:mod:`fdb_scraper.pipeline`, which decodes, joins and validates. A parse bug can
be reproduced from a saved export alone -- the reason the raw fields are also kept
in the scd2 history.

The export ships one XML document per programme below
:data:`CONTENT_SUBDIR`, plus separate trees for the documents a programme links
to; those are read by :mod:`fdb_scraper.links`, which shares the property readers
below. A document is a flat list of ``<property>`` elements, each carrying its
value as ``<text>`` (rich text), ``<value>`` (plain) or ``<links>`` (hrefs into
another tree), so the field maps in this module are the whole mapping from the CMS
vocabulary to column names.

Nothing here decodes: classifier hrefs stay full paths and labels stay as the CMS
wrote them, because resolving them needs the other trees and the codelists.

Self-contained on purpose: :func:`parse_programmes` needs only ``polars`` and the
standard library, so this module can be lifted out of the package and read or
copied on its own.
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import polars as pl

SITE_URL = "https://www.foerderdatenbank.de"
CONTENT_SUBDIR = "BMWI/FDB/Content/DE/Foerderprogramm"

XLINK = "{http://www.w3.org/1999/xlink}"
# Control characters that XML 1.0 forbids outright. At least one document ships
# a stray \x02 mid-word, which makes a strict parser reject the whole file.
CONTROL_RE = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
# SOFT HYPHEN plus the C0 and C1 control characters. Whitespace is left to
# WS_RE, which collapses it; see _clean.
INVISIBLE_RE = re.compile("[\u00ad\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
CLASSIFIER_NAME_RE = re.compile(r"/Common/([^/]+)$")

TEXT_FIELDS = {
    "gsb:title": "title",
    "gsb:summary": "summary",
    "gsb:teaserText": "teaser",
    "gsb:bodyText": "body_text",
    "gsb:header": "header",
    "gsb:remark": "remark",
    "gsb:comment": "comment",
    "gsb:challenge": "challenge",
    "gsb:competenceDescr": "competence_descr",
    "gsb:customerBenefit": "customer_benefit",
    "gsb:functions": "functions",
    "gsb:procDescription": "proc_description",
    "gsb:procInfluence": "proc_influence",
    "gsb:procMethod": "proc_method",
    "gsb:procQuality": "proc_quality",
    "gsb:progress": "progress",
    "gsb:referenceCustomer": "reference_customer",
    "gsb:regulatoryFWork": "regulatory_framework",
    "gsb:requirements": "requirements",
    "gsb:serviceDescription": "service_description",
    "gsb:serviceFeeDescr": "service_fee_descr",
    "gsb:termsOfPayment": "terms_of_payment",
}

VALUE_FIELDS = {
    "gsb:keywords": "keywords",
    "gsb:subType": "subtype",
    "gsb:externalID": "external_id",
}

DATE_FIELDS = {
    "gsb:dateOfIssue": "date_of_issue",
    "gsb:dateOfExpiration": "date_of_expiration",
}

CLASSIFIERS = [
    "Foerderart", "Foerderberechtigte", "Foerderbereich", "Foerdergeber",
    "Foerdergebiet", "Foerderorganisation", "Foerdertermin", "GRW",
    "BranchenExistenzgruenderin", "BranchenUnternehmen",
    "Unternehmensalter", "Unternehmensgroesse",
    "Kontakt", "ExternerLink",
    "UFArbeit", "UFAusWeiterbildung", "UFAussenwirtschaft", "UFBeratung",
    "UFEnergieeffizienz", "UFExistenzgruendung", "UFForschungOffen",
    "UFForschungSpezifisch", "UFFrauenfoerderung", "UFGesundheitSoziales",
    "UFInfrastruktur", "UFKulturMedienSport", "UFLandwirtschaft",
    "UFMessenAusstellungen", "UFRegionalfoerderung", "UFStaedtebauStadterneuerung",
    "UFUmweltNaturschutz", "UFUnternehmensfinanzierung", "UFWohnungsbau",
]


def _column_for(classifier: str) -> str:
    if classifier == "GRW":
        return "grw"
    if classifier.startswith("UF"):
        return "uf_" + re.sub(r"(?<!^)(?=[A-Z])", "_", classifier[2:]).lower()
    return re.sub(r"(?<!^)(?=[A-Z])", "_", classifier).lower()


COL_BY_CLS = {c: _column_for(c) for c in CLASSIFIERS}

LIST_FIELDS: tuple[str, ...] = ("languages", *COL_BY_CLS.values())

# Declared rather than inferred: a field that no parsed document populates would
# otherwise get no column at all, or a Null dtype instead of String/List(String),
# leaving the frame's schema dependent on which programmes were parsed.
EXPORT_SCHEMA: dict[str, pl.DataType] = {
    "programme_slug": pl.String,
    "path": pl.String,
    "url": pl.String,
    **{f: pl.String for f in TEXT_FIELDS.values()},
    **{f: pl.String for f in VALUE_FIELDS.values()},
    **{f: pl.Datetime(time_unit="us", time_zone="UTC") for f in DATE_FIELDS.values()},
    "should_not_be_indexed": pl.Int64,
    **{f: pl.List(pl.String) for f in LIST_FIELDS},
}

ALL_FIELDS: tuple[str, ...] = tuple(EXPORT_SCHEMA)


def parse_xml(path: Path) -> ET.Element:
    """Parse a document, stripping forbidden control characters if needed."""
    try:
        return ET.parse(path).getroot()
    except ET.ParseError:
        return ET.fromstring(CONTROL_RE.sub(b"", path.read_bytes()))


def _clean(text: str) -> str | None:
    """Drop invisible characters, collapse whitespace, return None if empty.

    The invisible ones are editing artefacts of the CMS, not content: they
    render as nothing and only break string comparison, search and downstream
    encoding. WS_RE handles the whitespace class -- ``\\s`` matches NO-BREAK
    SPACE and TAB -- so INVISIBLE_RE covers what is left: SOFT HYPHEN, which
    the export ships in 60 values, and the C0/C1 control characters, of which
    one U+0090 appears in a programme description.
    """
    return WS_RE.sub(" ", INVISIBLE_RE.sub("", text)).strip() or None


def strip_html(text: str | None) -> str | None:
    """Rich-text value as plain text: unescaped, unwrapped, tags removed.

    Public because the linked-document trees carry the same markup -- see
    :mod:`fdb_scraper.links`.
    """
    if not text:
        return None
    text = html_lib.unescape(text)
    text = text.replace("<![CDATA[", "").replace("]]>", "")
    return _clean(TAG_RE.sub(" ", text))


def _rich_text(prop: ET.Element) -> str | None:
    t = prop.find("text")
    return strip_html(t.text) if t is not None else None


def _value(prop: ET.Element) -> str | None:
    v = prop.find("value")
    # Cleaned like rich text: keywords arrive here and carry a literal tab.
    return _clean(v.text) if v is not None and v.text is not None else None


def _link_list(prop: ET.Element) -> list[str]:
    le = prop.find("links")
    if le is None:
        return []
    return [l.get(f"{XLINK}href", "") for l in le.findall("link")]


def _parse_iso(value: str | None) -> datetime | None:
    """Parse a timestamp, discarding anything unparseable.

    Returning the raw string instead would put a str in a Datetime column and
    fail the whole frame over one bad value.
    """
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _classified(prop: ET.Element) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    clls = prop.find("classifiedLinkLists")
    if clls is None:
        return out
    for ll in clls.findall("classifiedLinkList"):
        cls = ll.find("classifierLinks")
        if cls is None:
            continue
        cls_links = cls.findall("link")
        if not cls_links:
            continue
        m = CLASSIFIER_NAME_RE.search(cls_links[0].get(f"{XLINK}href", ""))
        if not m:
            continue
        le = ll.find("links")
        hrefs = [l.get(f"{XLINK}href", "") for l in le.findall("link")] if le is not None else []
        out.setdefault(m.group(1), []).extend(hrefs)
    return out


def parse_programme(path: Path, url: str) -> dict:
    """One programme document as a row, every field in ``ALL_FIELDS`` present."""
    root = parse_xml(path)
    # The document's @name: the programme's own slug, not a row identifier. The
    # same slug runs at several funding levels -- meistergruendungspraemie and
    # agrarinvestitionsfoerderungsprogramm are each three separate programmes.
    row: dict = {"programme_slug": root.get("name"), "path": str(path), "url": url}
    for prop in root.findall("property"):
        name = prop.get("name")
        if name in TEXT_FIELDS:
            row[TEXT_FIELDS[name]] = _rich_text(prop)
        elif name in VALUE_FIELDS:
            row[VALUE_FIELDS[name]] = _value(prop)
        elif name in DATE_FIELDS:
            row[DATE_FIELDS[name]] = _parse_iso(_value(prop))
        elif name == "gsb:shouldNotBeIndexed":
            try:
                row["should_not_be_indexed"] = int(_value(prop))
            except (TypeError, ValueError):
                row["should_not_be_indexed"] = None
        elif name == "gsb:languageInd":
            row["languages"] = _link_list(prop)
        elif prop.get("type") == "ClassifiedLinkLists":
            for cls_name, hrefs in _classified(prop).items():
                col = COL_BY_CLS.get(cls_name)
                if col:
                    row[col] = hrefs
    # Every field gets a key even when no document carries the property, so the
    # frame's columns depend on ALL_FIELDS rather than on which properties the
    # parsed documents happen to use.
    for col in LIST_FIELDS:
        row.setdefault(col, [])
    for col in ALL_FIELDS:
        row.setdefault(col, None)
    return row


def check_fields(fields: Iterable[str] | None = None) -> list[str]:
    """``fields`` as a list, or every field, refusing anything unparsed.

    Separate from :func:`parse_programmes` so a caller that downloads first can
    reject a typo before spending the download -- see
    :func:`fdb_scraper.scraper.scrape`.
    """
    selected = list(ALL_FIELDS) if fields is None else list(fields)
    unknown = set(selected) - set(ALL_FIELDS)
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")
    return selected


def parse_programmes(
    export_root: str | Path,
    fields: Iterable[str] | None = None,
) -> pl.DataFrame:
    """Every programme document under ``export_root``, one row each.

    No download and no database: this is the whole parse step, runnable on any
    extracted export. :func:`fdb_scraper.scraper.scrape` is this function with a
    download in front of it.

    Args:
        export_root: Root of an extracted export -- the directory that contains
            ``BMWI``.
        fields: Column names to keep; defaults to all of :data:`ALL_FIELDS`.

    Raises:
        ValueError: If ``fields`` names something the parser does not produce.
        FileNotFoundError: If there is no programme directory below the root.
        RuntimeError: If the programme directory holds no documents.
    """
    selected = check_fields(fields)
    root = Path(export_root)
    programmes = root / CONTENT_SUBDIR
    if not programmes.is_dir():
        raise FileNotFoundError(programmes)
    rows = []
    for xml_path in sorted(programmes.rglob("*.xml")):
        # Site URLs mirror the export tree below BMWI, with .xml -> .html.
        rel = xml_path.relative_to(root / "BMWI").with_suffix(".html")
        rows.append(parse_programme(xml_path, f"{SITE_URL}/{rel}"))
    if not rows:
        raise RuntimeError(f"no programme files under {programmes}")

    return pl.DataFrame(rows, schema=EXPORT_SCHEMA).select(selected)
