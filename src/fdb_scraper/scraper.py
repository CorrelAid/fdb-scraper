"""Extract the Förderdatenbank programme export into a Polars DataFrame."""

from __future__ import annotations

import html as html_lib
import re
import tempfile
import zipfile
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import polars as pl
import requests

EXPORT_URL = "https://www.foerderdatenbank.de/FDB/WS/export"
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


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    text = html_lib.unescape(text)
    text = text.replace("<![CDATA[", "").replace("]]>", "")
    return _clean(TAG_RE.sub(" ", text))


def _rich_text(prop: ET.Element) -> str | None:
    t = prop.find("text")
    return _strip_html(t.text) if t is not None else None


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


def _parse_programme(path: Path, url: str) -> dict:
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

    Args:
        fields: Column names to extract; defaults to all of ``ALL_FIELDS``.
        export_dir: Directory of an already extracted export. When omitted the
            export is downloaded into a temporary directory.
    """
    selected = list(ALL_FIELDS) if fields is None else list(fields)
    unknown = set(selected) - set(ALL_FIELDS)
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")

    with export(export_dir) as root:
        programmes = root / CONTENT_SUBDIR
        if not programmes.is_dir():
            raise FileNotFoundError(programmes)
        rows = []
        for xml_path in sorted(programmes.rglob("*.xml")):
            # Site URLs mirror the export tree below BMWI, with .xml -> .html.
            rel = xml_path.relative_to(root / "BMWI").with_suffix(".html")
            rows.append(_parse_programme(xml_path, f"{SITE_URL}/{rel}"))
        if not rows:
            raise RuntimeError(f"no programme files under {programmes}")

    return pl.DataFrame(rows, schema=EXPORT_SCHEMA).select(selected)
