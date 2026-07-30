"""Column -> RDF predicate, for describing the published table to consumers.

:mod:`fdb_scraper.schema` says what a column *contains* (dtype, pattern, closed
vocabulary). It cannot say what a column *means*: ``id``, ``id_url`` and
``id_hash`` are all non-null strings matching a slug pattern, and they identify
three different things. That judgement is made here, once, by hand.

The distinction that matters is reuse versus invention:

* :data:`EXTERNAL` -- an established term genuinely describes the column, so
  point at it. Consumers already understand ``dct:title`` and ``vcard:locality``.
* everything else -- no established term fits, so mint one under
  :data:`fdb_scraper.uris.VOCAB` rather than bend a foreign term out of shape.
  Same rule as
  :mod:`fdb_scraper.codelists`, where a category with no honest counterpart is
  left unmapped instead of folded into a near-miss.

A new published column therefore needs no edit to be describable -- it gets a
minted term -- but it will not gain a reused one by accident either. Import
raises if :data:`EXTERNAL` names a column that is no longer published, so a
rename cannot leave a mapping pointing at nothing.
"""

from __future__ import annotations

from fdb_scraper.schema import PIVOTED_SOURCES, PIVOTS, RENAMES
from fdb_scraper.schema import PUBLISHED_FIELDS
from fdb_scraper.uris import VOCAB
from fdb_scraper.generated import CLOSED_VOCABS

NAMESPACES = {
    "dcat": "http://www.w3.org/ns/dcat#",
    "dcatap": "http://data.europa.eu/r5r/",
    "dcatapde": "http://dcat-ap.de/def/dcatde/",
    "dct": "http://purl.org/dc/terms/",
    "fdb": VOCAB,
    "foaf": "http://xmlns.com/foaf/0.1/",
    "prov": "http://www.w3.org/ns/prov#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "vcard": "http://www.w3.org/2006/vcard/ns#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}

# Columns where a foreign term says exactly what the column holds. Anything not
# listed gets a minted ``fdb:`` term -- see :func:`predicate`.
EXTERNAL = {
    # Identity. ``id_url`` is the join key, so it carries dct:identifier;
    # ``programme_slug`` is shared by up to three Länder and ``id_hash`` is a
    # digest of ``id_url``, so neither is an identifier of this record.
    "id_url": "dct:identifier",
    # The rendered detail page this row was derived from.
    "url": "foaf:page",
    "title": "dct:title",
    "description": "dct:description",
    # Kurzzusammenfassung > Kurztext: a summary of the full description.
    "short_description": "dct:abstract",
    # A citation line, e.g. the Richtlinie's publication reference.
    "legal_citation": "dct:bibliographicCitation",
    # No reused term for seo_description or application_language, so both get a
    # minted one. skos:note would be wrong for the first -- it is a search-engine
    # description of the page, not an editorial note about the concept -- and
    # dct:language is about the language of the resource, not of the application
    # a reader has to write.
    "date_of_issue": "dct:issued",
    # Bundesland (plus "_bundesweit", which is a scope rather than a Land, but
    # still a spatial extent).
    "funding_location": "dct:spatial",
    "further_links": "rdfs:seeAlso",
    # DCAT-AP.de's own term for "the attribution text a reuser must reproduce",
    # which is exactly what the column holds. Its domain is a dataset rather than a
    # record, so this stretches the term one level down -- still closer than
    # dct:license, which wants a licence document, or dct:rights, which is about
    # rights rather than the credit line.
    "license_info": "dcatapde:licenseAttributionByText",
    # When the export's content for this programme last changed. dct:modified is
    # about the resource, which is what this records -- unlike on_website_from and
    # deleted, which are facts about our observation of it and get minted terms.
    "last_updated": "dct:modified",
    # Contacts. The flat address terms below are the legacy vCard ones carried
    # into the 2006 namespace; the structured vcard:hasAddress form cannot be
    # expressed in a flat table, and DCAT-AP.de uses the same flat terms.
    "contact_info_institution": "vcard:fn",
    "contact_info_email": "vcard:hasEmail",
    "contact_info_phone": "vcard:hasTelephone",
    "contact_info_website": "vcard:hasURL",
    "contact_info_road": "vcard:street-address",
    "contact_info_zip_code": "vcard:postal-code",
    "contact_info_city": "vcard:locality",
    "contact_info_state": "vcard:region",
    "contact_info_country": "vcard:country-name",
    "contact_info_post_box": "vcard:post-office-box",
    # No vcard term: fax and mobile are types on vcard:hasTelephone rather than
    # predicates of their own, and a flat column cannot carry the type.
}

# Published column -> the concept scheme its values come from. Derived rather
# than typed, so a new closed vocabulary is picked up without an edit here. The
# 19 uf_* vocabularies are excluded because publish.add_subareas folds them into
# one column of "area.subarea" paths, which needs a scheme of its own.
# Published column -> the concept scheme its values come from. Derived rather
# than typed, so a new closed vocabulary is picked up without an edit here. The
# pivoted vocabularies are excluded: publish.add_pivoted folds each group into one
# column of "parent.child" paths, which needs a scheme of its own.
SCHEMES = {
    **{RENAMES.get(f, f): f for f in CLOSED_VOCABS if f not in PIVOTED_SOURCES},
    **{target: target for target in PIVOTS},
}

# Scheme name -> the vocabulary its child codes come from, for the generator. A
# pivot's children all share one vocabulary, so any source column will do.
SCHEME_VOCAB = {
    **{f: f for f in CLOSED_VOCABS if f not in PIVOTED_SOURCES},
    **{target: next(iter(parents)) for target, parents in PIVOTS.items()},
}

# Minted terms that describe a *column* rather than hold a value of one, so they
# are not in PREDICATES and no published column maps to them. Kept here so the
# generator and the "every minted term is defined" test agree on which terms the
# vocabulary document is expected to carry beyond the column predicates.
#
# fdb:origin is the one thing the CSV cannot show: upstream, derived or inferred.
# See :func:`fdb_scraper.schema.origin`.
ORIGIN_TERM = "fdb:origin"
ANNOTATIONS = (ORIGIN_TERM,)


def _camel(column: str) -> str:
    head, *rest = column.split("_")
    return head + "".join(part.title() for part in rest)


def predicate(column: str) -> str:
    """CURIE for ``column``, reusing a foreign term where one honestly fits."""
    return EXTERNAL.get(column, f"fdb:{_camel(column)}")


def expand(curie: str) -> str:
    prefix, _, local = curie.partition(":")
    return NAMESPACES[prefix] + local


PREDICATES = {f: predicate(f) for f in PUBLISHED_FIELDS}

_unknown_prefix = sorted(
    c for c in PREDICATES.values() if c.partition(":")[0] not in NAMESPACES
)
if _unknown_prefix:  # pragma: no cover -- guards a typo in EXTERNAL
    raise RuntimeError(f"predicates with an undeclared prefix: {_unknown_prefix}")

_stale = sorted(set(EXTERNAL) - set(PUBLISHED_FIELDS))
if _stale:  # pragma: no cover -- guards a column renamed out from under EXTERNAL
    raise RuntimeError(f"EXTERNAL maps columns that are not published: {_stale}")
