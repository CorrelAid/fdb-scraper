"""Resolve the documents a Förderprogramm links to.

Contacts, addresses and external links live in their own document trees in the
export; a programme only points at them:

    Foerderprogramm.kontakt -> Kontakt -> cl2Addresses -> Adresse
                                       -> website      -> ExternerLink -> gsb:url
    Foerderprogramm.externer_link -> ExternerLink -> gsb:url

Those targets are not as tidy as the field names suggest: ``externer_link``
sometimes points at a ``Kontakt`` or ``Archiv`` document and ``kontakt``
sometimes at a ``Foerdergeber`` one. Since every tree shares the same property
vocabulary, everything outside ``Foerderprogramm`` goes into a single index and
each link is resolved against that.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from fdb_scraper.scraper import SITE_URL, XLINK, _strip_html, parse_xml

CONTENT_DIR = "BMWI/FDB/Content/DE"
# Everything except the programmes themselves, which are parsed by scraper.py.
LINKED_TREES = ("ExternerLink", "Kontakt", "Adresse", "Foerdergeber", "Archiv", "Download")

CONTACT_FIELDS = {
    "gsb:title": "institution",
    "gsb:email": "email",
    "gsb:phone": "phone",
    "gsb:fax": "fax",
    "gsb:mobile": "mobile",
}
ADDRESS_FIELDS = {
    "gsb:road": "road",
    "gsb:houseId": "house_id",
    "gsb:building": "building",
    "gsb:zipCode": "zip_code",
    "gsb:city": "city",
    "gsb:postBox": "post_box",
    "gsb:state": "state",
    "gsb:country": "country",
}
DOC_FIELDS = {**CONTACT_FIELDS, **ADDRESS_FIELDS, "gsb:url": "url"}
DOC_LINKS = {"gsb:cl2Addresses": "_address", "gsb:website": "_website"}

CONTACT_KEYS = (*CONTACT_FIELDS.values(), "website", *ADDRESS_FIELDS.values())


def _text(prop: ET.Element) -> str | None:
    """Value of a property, whether stored as <value> or as RichText."""
    v = prop.find("value")
    if v is not None and v.text is not None:
        return _strip_html(v.text)
    t = prop.find("text")
    return _strip_html(t.text) if t is not None else None


def _hrefs(prop: ET.Element) -> list[str]:
    return [
        href.removeprefix("target:")
        for link in prop.iter("link")
        # Skip the classifier itself; only its targets are of interest.
        if "/Classifications/Classifier/" not in (href := link.get(f"{XLINK}href", ""))
    ]


def _read(path: Path) -> dict:
    out: dict = {}
    for prop in parse_xml(path).findall("property"):
        name = prop.get("name")
        if name in DOC_FIELDS:
            out[DOC_FIELDS[name]] = _text(prop)
        elif name in DOC_LINKS:
            hrefs = _hrefs(prop)
            out[DOC_LINKS[name]] = hrefs[0] if hrefs else None
    return out


def _key(path: Path, root: Path) -> str:
    """Document key in the form used by hrefs, minus the ``target:`` prefix."""
    return "/" + str(path.relative_to(root).with_suffix(""))


def page_url(key: str) -> str:
    """Public URL of a document. Site paths mirror the export tree below BMWI."""
    return f"{SITE_URL}{key.removeprefix('/BMWI')}.html"


def resolve(export_root: str | Path) -> dict[str, dict]:
    """Index every linked document by href, with addresses and websites merged in."""
    root = Path(export_root)
    content = root / CONTENT_DIR

    # rglob on a missing directory yields nothing, which would look like an
    # export without contacts rather than a wrong path.
    if not content.is_dir():
        raise FileNotFoundError(content)

    docs: dict[str, dict] = {}
    for tree in LINKED_TREES:
        for path in (content / tree).rglob("*.xml"):
            docs[_key(path, root)] = _read(path)

    for key, doc in docs.items():
        address = docs.get(doc.pop("_address", None) or "", {})
        website = doc.pop("_website", None)
        doc.update({f: address.get(f) for f in ADDRESS_FIELDS.values() if f not in doc})
        # A website link points at another document; its own gsb:url if it has
        # one, else that document's page on the site.
        doc["website"] = (
            (docs.get(website, {}).get("url") or page_url(website)) if website else None
        )
        # Same for the link target itself: documents without a gsb:url of their
        # own (Kontakt, Archiv, Foerdergeber) are still pages on the site.
        doc.setdefault("url", None)
        doc["url"] = doc["url"] or page_url(key)
        # Every document exposes the full key set, so a consumer reading a field
        # a document happens to lack gets a null rather than a KeyError.
        for field in CONTACT_KEYS:
            doc.setdefault(field, None)

    return docs
