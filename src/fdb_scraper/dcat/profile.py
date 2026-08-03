"""The RDF vocabularies the published documents are written in.

Namespace objects for the builders, and the helper that turns a code of an EU
authority vocabulary into the IRI DCAT-AP.de expects. No content: what is *said*
about the dataset is declared in :mod:`fdb_scraper.config`.

Every URI here is read from :data:`fdb_scraper.config.NAMESPACES`, which is the one
place a prefix is bound -- the generated documents bind their prefixes from the same
mapping, so a namespace declared in a document and a namespace a builder wrote
triples in cannot be two different URIs.
"""

from __future__ import annotations

from rdflib import Namespace, URIRef

from fdb_scraper.config import NAMESPACES

AUTHORITY = "http://publications.europa.eu/resource/authority/"

# DCAT-AP.de's political levels, which are not an EU authority vocabulary and have
# a namespace of their own rather than a code list to resolve against.
POLITICAL_LEVEL = "http://dcat-ap.de/def/politicalGeocoding/Level/"

# IANA's registry, where a media type is dereferenceable.
MEDIA_TYPE = "https://www.iana.org/assignments/media-types/"

DCAT = Namespace(NAMESPACES["dcat"])
DCATAP = Namespace(NAMESPACES["dcatap"])
DCATAPDE = Namespace(NAMESPACES["dcatapde"])
DCT = Namespace(NAMESPACES["dct"])
FDB = Namespace(NAMESPACES["fdb"])
FOAF = Namespace(NAMESPACES["foaf"])
PROV = Namespace(NAMESPACES["prov"])
VCARD = Namespace(NAMESPACES["vcard"])

# Only the vocabulary document needs owl, so it is bound there rather than in
# semantics.NAMESPACES, which every generated file binds from.
OWL = Namespace("http://www.w3.org/2002/07/owl#")


def authority(scheme: str, code: str) -> URIRef:
    """One code of an EU authority vocabulary, as the IRI DCAT-AP.de expects."""
    return URIRef(f"{AUTHORITY}{scheme}/{code}")
