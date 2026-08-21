"""
Sections, in reading order: where this is published, where it came from, what it is
licensed as, which vocabularies describe it, what is said about it, which fields are
kept, which codelists are linked.
"""

from __future__ import annotations

from datetime import datetime, timezone

# =============================================================================
# Where this is published
# =============================================================================
#
# Two kinds of string, and they are not interchangeable:
#
# *identifiers* -- BASE, VOCAB, DATASET. What a dataset, a distribution, an agent
#   or a minted property *is called*.
# *locations* -- DATASET_DOC_URL, SCHEMA_URL, DOWNLOAD_BASE. Where the bytes are
#   fetched from.
#
# Both kinds resolve against HOST, deliberately: the identifiers have to be
# dereferenceable to be worth minting, and the only server that answers for them is
# the one this repo deploys. ``Caddyfile`` serves ``dcat/`` at the server root and
# negotiates ``/def/`` and ``/id/`` there, which is what makes ``/def/fdb`` return
# the vocabulary rather than a 404.
#
# Hardcoded rather than read from the environment on purpose. ``dcat/`` is
# generated *and committed* -- an environment variable would make the committed
# artefact depend on whoever last ran the generator, and the tests assert the exact
# URIs because a wrong one is not a runtime error, it is a published mistake. A
# change here shows up as a reviewable diff across ``dcat/`` and the tests, which is
# the intended amount of friction for changing an identifier.

# The deployed host.
HOST = "https://fdb.cdl.correlaid.org"

# --- Identifiers (permanent) -------------------------------------------------

# Namespace for everything this dataset names: datasets, distributions, agents,
# individual programmes.
BASE = f"{HOST}/id/"

# The one dataset this repository publishes. Also the last path segment of the
# document an aggregating catalogue harvests -- the two must not be spelled
# separately.
DATASET_ID = "foerderdatenbank-programme"
DATASET = f"{BASE}dataset/{DATASET_ID}"

# Namespace for terms no established vocabulary describes -- see
# :mod:`fdb_scraper.semantics`. A hash namespace, so ``dcat/def/fdb.ttl`` served
# at ``/def/fdb`` makes every term in it dereferenceable.
VOCAB = f"{HOST}/def/fdb#"

# What one row is about. These 2500 do not resolve -- one document per programme is
# a publishing decision not taken -- but the URI is still the right thing to state:
# it is how two consumers agree which programme they mean.
RECORD_URI = BASE + "programme/{id_url}"

# --- Locations ---------------------------------------------------------------

# The whole harvesting interface: a self-contained description of the one dataset,
# with its distributions, publisher and contact point in the same graph. This
# repository publishes no ``dcat:Catalog`` -- the catalogue that lists this dataset
# alongside the rest of the Civic Data Lab's is built and served elsewhere, and it
# fetches this document. Named with the extension rather than relying on the
# content-negotiated ``/id/dataset/<id>``, so a harvester that sends no Accept
# header still gets Turtle.
DATASET_DOC_URL = f"{DATASET}.ttl"
# The CSVW column contract, referenced from the distribution with dct:conformsTo.
SCHEMA_URL = f"{HOST}/table-schema.json"
# Where scripts/build_dist.py writes the published files.
DOWNLOAD_BASE = f"{HOST}/data/"


# =============================================================================
# Where the data came from
# =============================================================================

# The endpoint that hands back the zip. The only URL the pipeline fetches.
EXPORT_URL = "https://www.foerderdatenbank.de/FDB/WS/export"

# The front page, cited as provenance rather than republished: the export itself is
# not a distribution here, so ``prov:wasDerivedFrom`` is the only thing tying a
# published table back to the bytes it was built from. Upstream serves only the
# current export, so that link identifies the process, not the exact input -- see
# the retention entry in README.md.
#
# Carried as ``foaf:page`` and *not* ``dct:source``: DCAT-AP 3.0 ranges
# ``dct:source`` over ``dcat:Dataset`` ("a related Dataset from which the described
# Dataset is derived"), and the Förderdatenbank's front page is a web page, not a
# catalogued dataset. 2.1.1 left the range open, so this passed then and is a
# violation now. ``foaf:page`` wants a ``foaf:Document``, which the page is, and
# which the published document states so it stays self-contained.
SOURCE_HOMEPAGE = "https://www.foerderdatenbank.de/"

# Every programme's detail page lives below this prefix; the schema checks it.
URL_PREFIX = r"^https://www\.foerderdatenbank\.de/FDB/Content/DE/Foerderprogramm/"


# =============================================================================
# What it is licensed as
# =============================================================================
#
# From the Förderdatenbank imprint, which licenses the site's texts under CC BY-ND
# and names the ministry as rights holder. Read by three places that must not
# disagree: the per-row attribution :mod:`fdb_scraper.process` writes, the schema
# check that every row carries it, and the DCAT metadata's ``dct:license`` and
# ``dcatapde:licenseAttributionByText``.
#
# ND is why nothing in the pipeline rewrites a value: the published table reformats
# the export into columns and resolves its internal links, which is not a derivative
# work of the texts, but editing them would be.
#
# funding_crawler recorded CC BY-ND 3.0 DE and "Wirtschaft und Klimaschutz". Both
# are now out of date -- the imprint says 4.0 and the ministry was renamed -- so
# these are taken from the current imprint rather than carried over.

# The rights holder, as the imprint names them. Also the attribution text a reuser
# of the whole dataset has to reproduce.
LICENSOR = "Bundesministerium für Wirtschaft und Energie"
# The licence, as each row's attribution line spells it and the schema checks for it.
LICENCE_LABEL = "CC BY-ND 4.0"
# The deed, for a human reading a cell.
LICENCE_DEED_URL = "https://creativecommons.org/licenses/by-nd/4.0/deed.de"
# The same licence as DCAT-AP.de names it, for ``dct:license`` on the distribution.
LICENCE_URI = "http://dcat-ap.de/def/licenses/cc-by-nd/4.0"


# =============================================================================
# Which vocabularies describe it
# =============================================================================

# Every prefix any generated document binds, and the one place a namespace URI is
# spelled: fdb_scraper.dcat.profile builds its rdflib Namespace objects from this,
# so a prefix declared in a document and a namespace a builder wrote triples in
# cannot be two different URIs.
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
# listed gets a minted ``fdb:`` term -- see :func:`fdb_scraper.semantics.predicate`.
# The distinction that matters is reuse versus invention: an established term is
# pointed at because consumers already understand it, and where none fits, a term is
# minted rather than a foreign one bent out of shape. Same rule as :data:`LINKED`
# below, where a category with no honest counterpart is left unmapped instead of
# folded into a near-miss.
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
    # about the resource, which is what this records -- unlike on_website_from,
    # on_website_to, and absent, which are facts about our observation of it and
    # get minted terms.
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
    "contact_info_country": "vcard:country-name",
    "contact_info_post_box": "vcard:post-office-box",
    # No vcard term: fax and mobile are types on vcard:hasTelephone rather than
    # predicates of their own, and a flat column cannot carry the type.
}

# The one minted term that describes a *column* rather than holding a value of one:
# where the column's values come from, which is the single thing no CSV can show.
ORIGIN_TERM = "fdb:origin"

# The class the minted properties describe. One record of the table is one
# Förderprogramm; the CSVW schema's aboutUrl names the same thing.
RECORD_CLASS = "Foerderprogramm"

# id_hash is the only column the schema declares unique that is not derived from a
# URL; url is unique too but unwieldy as a key.
PRIMARY_KEY = "id_hash"


# =============================================================================
# What is said about it
# =============================================================================

# --- The measured facts the prose quotes -------------------------------------
#
# Stated once because they appear in both descriptions and in the column note, and
# a metric that disagrees with itself across two of them is a claim no reader can
# check. From services/keyword_segmenter/RESULTS.md; update there and here
# together, and the published prose follows.
METRICS = {
    # Exact-span F1 of the shipped segmenter on the held-out hand-labelled split.
    "f1": 0.967,
    # The same metric for the baseline that never joins anything, which is what
    # makes the number above mean something.
    "baseline_f1": 0.878,
    # Share of non-null ``keywords`` values carrying no separator signal at all,
    # which is why the column cannot be split by a rule.
    "no_separator_pct": 87.7,
}


def _de(value: float) -> str:
    """A number as German prose writes it: decimal comma."""
    return f"{value}".replace(".", ",")


# --- Who publishes this ------------------------------------------------------

PUBLISHER = {
    "name": "CorrelAid e.V.",
    "homepage": "https://correlaid.org/",
    "email": "mailto:info@correlaid.org",
}

# Where a reader goes to see how this dataset is built.
LANDING_PAGE = "https://github.com/CorrelAid/fdb_scraper"

# --- What it is called and what it says --------------------------------------

DATASET_TITLE = {
    "de": "Förderprogramme aus der Förderdatenbank des Bundes",
    "en": "Funding Programmes from the German funding database",
}

# ``{fields}`` is the number of published columns, filled in by the builder. Typed
# out, it was already wrong by five when the history columns were added, and a
# description that contradicts the schema it ships beside is worse than one without
# a number.
DESCRIPTION_DE = f"""\
Alle Förderprogramme der Förderdatenbank des Bundes (Bund, Länder und EU), ein \
Datensatz pro Programm mit {{fields}} Feldern.

Die Bundesregierung bietet mit der Förderdatenbank eine Suchmaschine für \
Förderungen an, veröffentlicht den zugrundeliegenden Datensatz aber nicht als Open Data im normativen Sinne. \

Grundlage ist der XML-Programmexport der Förderdatenbank.

Der Export beinhaltet immer nur den heutigen Stand. Dieser Datensatz führt daher eine \
Historie: jedes Programm trägt, wann es zuerst erfasst wurde \
(``on_website_from``), wann sich sein Inhalt zuletzt geändert hat \
(``dct:modified``), alle bisherigen Änderungszeitpunkte \
(``previous_update_dates``) und wann es den Export verlassen hat \
(``on_website_to``). Programme, die aus der Förderdatenbank \
verschwinden, bleiben mit ihrem letzten bekannten Inhalt und \
``absent = true`` enthalten. Der Datensatz umfasst damit mehr Programme als \
der Export und wächst über die Zeit.

Eine Spalte ist modellbasiert und nicht Teil der Quelle: ``keywords_extracted`` \
enthält die Stichwörter, die ein finetuned Sprachmodell aus der \
Rohspalte ``keywords`` herausliest. Upstream sind sie durch Leerzeichen \
verbunden und in {_de(METRICS["no_separator_pct"])} % der Fälle nicht trennbar. \
Jedes Stichwort ist \
nachweislich ein zusammenhängender Abschnitt des Originalstrings, geprüft bei \
jeder Veröffentlichung; die Grenzziehung selbst ist eine Modellentscheidung \
(F1 {_de(METRICS["f1"])} auf einer Handstichprobe). ``keywords`` bleibt unverändert daneben \
stehen. Das Tabellenschema kennzeichnet jede Spalte mit ``fdb:origin`` als \
``upstream``, ``derived`` oder ``inferred``.\
"""

# Manual translation of DESCRIPTION_DE, paragraph for paragraph. Keep in lockstep:
# edit one, edit the other.
DESCRIPTION_EN = f"""\
All funding programmes in the German federal funding database \
(Förderdatenbank; federal, state and EU programmes), one record per programme \
with {{fields}} fields.

The federal government provides the Förderdatenbank as a search engine for \
funding programmes, but does not publish the underlying dataset as Open Data in \
the normative sense.

The basis is the Förderdatenbank's XML programme export.

The export only ever contains today's state. This dataset therefore maintains \
a history: each programme carries when it was first recorded \
(``on_website_from``), when its content last changed (``dct:modified``), \
all previous change timestamps (``previous_update_dates``) and when it left \
the export (``on_website_to``). Programmes that disappear from the \
Förderdatenbank are retained with their last known content and \
``absent = true``. The dataset thus covers more programmes than the \
export and grows over time.

One column is model-produced and not part of the source: ``keywords_extracted`` \
holds the keywords a finetuned language model reads out of the raw ``keywords`` \
column. Upstream they are joined with spaces and carry no separator at all in \
{METRICS["no_separator_pct"]}% of cases. Every keyword is verifiably a \
contiguous span of the original string, checked on every publish; where the \
boundaries fall is the model's judgement (F1 {METRICS["f1"]} on a hand-labelled \
sample). ``keywords`` is published unchanged beside it. The table schema marks \
every column with ``fdb:origin`` as ``upstream``, ``derived`` or ``inferred``.\
"""

DESCRIPTION = {"de": DESCRIPTION_DE, "en": DESCRIPTION_EN}

KEYWORDS_DE = (
    "Förderung",
    "Förderprogramme",
    "Förderdatenbank",
    "Fördermittel",
)

# --- Which controlled values this dataset claims -----------------------------
#
# Codes of EU authority vocabularies, resolved to IRIs by
# :func:`fdb_scraper.dcat.profile.authority`. Each is a choice: WEEKLY is the
# schedule the export is refetched on, ECON and GOVE are the two data themes
# funding programmes fall under.
AUTHORITY_CODES = {
    "language": "DEU",
    "access_right": "PUBLIC",
    "frequency": "WEEKLY",
    "availability": "AVAILABLE",
    "themes": ("ECON", "GOVE"),
}

# DCAT-AP.de's own levels, which are not an EU authority vocabulary and so carry
# their own namespace. Both are claimed because funding programmes are run by the
# federal government, the Länder and the EU.
POLITICAL_LEVELS = ("federal", "state")

# --- The one distribution ----------------------------------------------------
#
# CSV with nested values JSON-encoded in their cell; the encoding is declared per
# column in the linked table-schema, so a consumer knows which cells to parse.
DISTRIBUTIONS = (
    {
        "slug": "csv",
        "file": "programme.csv",
        "title_de": "Förderprogramme als CSV-Datei",
        "desc_de": "Die vollständige Tabelle mit historischen Einträgen. Das verlinkte Tabellenschema beschreibt Datentypen, "
        "Pflichtfelder und Muster; Spalten mit Listenwerten enthalten JSON-Arrays als Zellenwert.",
        "file_type": "CSV",
        "media_type": "text/csv",
        "conforms_to": SCHEMA_URL,
    },
)

# --- The minted vocabulary's own prose ---------------------------------------

RECORD_LABEL = {"de": "Förderprogramm", "en": "funding programme"}

RECORD_COMMENT_DE = (
    "Ein Förderprogramm der Förderdatenbank des Bundes; eine Zeile der "
    "veröffentlichten Tabelle."
)

ONTOLOGY_TITLE_DE = "Förderdatenbank -- minted Begriffe"
ONTOLOGY_DESCRIPTION_DE = (
    "Begriffe für die Spalten der Förderprogramm-Tabelle, für die kein "
    "etablierter Begriff passt."
)

# What ``fdb:origin`` means. ``{inferred}`` is the inferred columns, filled in from
# :data:`INFERRED_COLUMNS`, so a second inferred column cannot be left out of its
# own definition.
ORIGIN_COMMENT_EN = (
    'Where a column\'s values come from: "upstream" -- stated by the source export '
    'and only reshaped; "derived" -- computed from upstream values or from the load '
    'history by a rule, exactly and reproducibly; "inferred" -- produced by a '
    "machine-learning model, therefore neither deterministic nor exact. The "
    "inferred columns are {inferred}; their accuracy is measured against a "
    "labelled sample rather than guaranteed."
)

# --- The CSVW document's own prose -------------------------------------------

TABLE_SCHEMA_DESCRIPTION_DE = (
    "Spaltenvertrag der CSV-Distribution, generiert aus dem "
    "Pandera-Schema der Pipeline."
)

# --- Per-column notes --------------------------------------------------------
#
# What an inferred column is, said in the two places a consumer looks: the CSVW
# column description and the minted term's comment. Per column, because the next
# inferred column will not be inferred the same way or to the same accuracy.
INFERRED_NOTES = {
    "keywords_extracted": (
        "inferred, not stated by the source: the raw keywords string split into "
        "single keywords by a fine-tuned German encoder, because upstream joins "
        f"them with spaces and {METRICS['no_separator_pct']}% of values carry no "
        "separator at all. Every "
        "keyword is a contiguous span of the raw string and the spans cover it "
        "exactly, which is checked on every publish, so nothing is invented, "
        "dropped or reordered; where the boundaries fall is the model's judgement "
        f"-- exact-span F1 {METRICS['f1']} on a held-out hand-labelled sample, "
        f"against {METRICS['baseline_f1']} "
        "for splitting on nothing. Null where a value has not been segmented. The "
        "raw keywords column is published unchanged beside it."
    ),
}

# Columns whose name and datatype do not say what the value means. Two kinds:
# identity, where several slug-shaped strings identify different things
# (:data:`EXTERNAL` makes the same point), and the history columns, which are facts
# about *our* observation of the export rather than statements the export makes -- a
# consumer reading them as source facts would be wrong, and nothing else in the file
# corrects that.
#
# Only these. A note repeating the column name is worse than none: it makes a
# reader who dereferenced a term for an explanation read one anyway to find there
# is none. Everything else is either self-evident from name plus datatype, or
# carries a reused term whose own definition already answers the question.
MEANING_NOTES = {
    "programme_slug": (
        "last path segment of the source URL. Not unique -- a programme offered by "
        "several Länder appears once per Land with the same slug -- so it does not "
        "identify a row; id_url does"
    ),
    "id_url": (
        "the source URL's path below .../Foerderprogramm/, without the .html "
        "suffix, remaining slashes replaced by \"-\" and lowercased. The join key "
        "across distributions, and the segment interpolated into the row's URI. "
        "Carried over unchanged from the previously published funding_crawler "
        "dataset, so identifiers stay comparable across the two"
    ),
    "id_hash": (
        "md5 of id_url, hex. Primary key of the published table and the key the "
        "version history groups by. A digest of the identifier rather than an "
        "identifier of its own: it is stable exactly as long as the source URL is"
    ),
    "on_website_from": (
        "first run in which any version of this programme was present in the "
        "export. An observation of ours, not a date the source states: the export "
        "ships only its current state, so nothing before this dataset's first load "
        "is visible and a programme older than that reads as first seen then"
    ),
    "last_updated": (
        "run in which this programme's export content last changed. Observed by "
        "comparing loads, because the export states no modification date of its own. "
        "Null for a programme that has not changed since it was first seen. For "
        "absent programmes, this is the last content change, not the absence date "
        "(which is on_website_to)"
    ),
    "previous_update_dates": (
        "one timestamp per content change, ascending: the run in which a new version "
        "superseded the previous one. Empty for a programme that has never changed; "
        "its length is therefore the number of content changes observed. For absent "
        "programmes, excludes the final transition (the absence), containing only "
        "actual content changes"
    ),
    "on_website_to": (
        "run in which this programme left the export. Null for programmes still "
        "present. Observed by comparing loads. For absent programmes, this may be "
        "much later than the last content change (last_updated)"
    ),
    "absent": (
        "true when no version of this programme is current any more: it was in an "
        "earlier export and is absent from the latest one. The row is kept, "
        "carrying the values of its last seen version, so the dataset outlives "
        "what upstream removed -- on_website_to then says when that happened. "
        "Derived from our load history, not stated by the source, and absence is "
        "all it means: a programme may be gone because it ended, because it was "
        "reorganised, or because upstream restructured the export. A programme that "
        "reappears goes back to false"
    ),
}


# =============================================================================
# Which fields are kept, and under which names
# =============================================================================

# Fields carrying no usable information in the export. Kept out of the default
# field list rather than deleted from the parser, so a field that starts being
# populated upstream can be picked up again by asking for it explicitly.
DROPPED_FIELDS = frozenset(
    {
        # Temporary extraction directory -- meaningless once the run ends.
        "path",
        # Never populated: Null dtype across every programme.
        "challenge",
        "customer_benefit",
        "proc_quality",
        "requirements",
        "service_description",
        "service_fee_descr",
        "terms_of_payment",
        # Always an empty link list.
        "foerdertermin",
        # Single value ("Deutsch") on all but three programmes.
        "languages",
        # Mostly nonsense: of 638 non-null values, 480 fall outside any
        # plausible range (the minimum is year 0207).
        "date_of_expiration",
        # Mix of year labels ("01".."20") and buckets ("nicht_relevant"),
        # dominated by "nicht_relevant"; not usable as stated.
        "unternehmensalter",
        # Internal editorial ticket ids ("# 861223"), non-null on 2495 of 2500.
        "comment",
        # Opaque 14-digit numbers ("99102158080000") on 10 programmes, with
        # nothing upstream that says what registry they belong to or how to
        # resolve them.
        "external_id",
        # The CMS's robots noindex flag: 0 on 2023 programmes, absent on 477,
        # never 1. The 0-vs-absent split records whether the CMS wrote the
        # property, not anything about the programme -- the two groups are
        # indistinguishable on every other column. Notebook E9 watches `raw` for
        # upstream starting to set it, which would plausibly mean a withdrawal.
        "should_not_be_indexed",
        # Constant: "ServiceOffer-FundingProgram" on 2497 programmes, absent on 3.
        # Those 3 are exactly the rows where `title` is null (E12), so the only
        # usable thing about the column duplicates a check consumers are already
        # told to make.
        "subtype",
        # gsb:referenceCustomer holds the funding ministry as a display string
        # ("Bundesministerium für Wirtschaft und Klimaschutz (BMWK)") on 25
        # programmes -- and every one of those rows already carries the same body
        # in foerderorganisation as a code. A label for something the table
        # states elsewhere, on 1% of rows.
        "reference_customer",
    }
)

# Fields the pipeline needs as input but does not publish. Distinct from
# DROPPED_FIELDS, which is never parsed at all: these have to survive the scrape
# because a later step consumes them.
#
# externer_link holds "target:/BMWI/..." document references. add_links resolves
# them against the linked-document index and publishes the result as
# further_links, so the raw references are an intermediate, not an output.
CONSUMED_FIELDS = frozenset({"externer_link"})

# Upstream ships two category records for nationwide scope, "_bundesweit" (577
# programmes) and "bundesweit" (85). Their XML is byte-identical apart from the
# name, and one programme carries both, so they are a duplicate rather than two
# concepts. "_bundesweit" is canonical: it is the one with a label entry in the
# export and the one the website's filter sidebar offers, the underscore sorting
# it above the Bundesländer. Kept as an explicit alias so the collapse is
# visible and reversible; every other upstream oddity is left verbatim.
CODE_ALIASES: dict[str, dict[str, str]] = {"foerdergebiet": {"bundesweit": "_bundesweit"}}

# Export field -> published name. Page section noted where the name is not
# self-explanatory.
RENAMES = {
    # Kurzzusammenfassung > Kurztext / Volltext
    "teaser": "short_description",
    "summary": "description",
    # Rechtsgrundlage > Richtlinie, plus its citation line
    "body_text": "legal_basis",
    "proc_description": "legal_citation",
    # Zusatzinfos, one column per sub-section
    "regulatory_framework": "legal_requirements",
    "proc_method": "procedure",
    "proc_influence": "deadlines",
    "progress": "processing_time",
    "competence_descr": "required_documents",
    # Categories, keeping the names the previous dataset published
    "foerderart": "funding_type",
    "foerderbereich": "funding_area",
    "foerdergebiet": "funding_location",
    "foerderberechtigte": "eligible_applicants",
    "foerdergeber": "funding_body",
    "foerderorganisation": "funding_organisation",
    # Editorial status note, e.g. "Programm aktiv, Antragstellung nicht möglich"
    "header": "status_note",
    # gsb:functions holds the language the application has to be written in --
    # "Deutsch", "Die Antragssprache für Skizzen ist in der Regel Englisch". 11
    # distinct values on 25 programmes. It is also where the usable half of the
    # language information ended up: the languages classifier was dropped for
    # saying only "Deutsch", while this states the exceptions.
    "functions": "application_language",
    # gsb:remark holds the page's search-engine description -- second person,
    # call to action ("Beantragen Sie als Konsortium Förderung für ..."). Written
    # for a search result, not an editorial note about the programme.
    "remark": "seo_description",
    "kontakt": "contact_ids",
}

# The prefix every resolved contact column carries. grw and unternehmensgroesse
# keep their export names: GRW is the name of a law, and "Unternehmensgröße"
# buckets are defined by it rather than translatable.
CONTACT_PREFIX = "contact_info_"

# --- Columns the export ships pivoted ----------------------------------------
# Two taxonomies arrive spread across one column per parent value, because the
# export models each parent as its own classifier. Both are republished as a
# single column of "parent.child" paths.
#
# The parent has to travel in the value, in both cases for the same reason: the
# child vocabulary is shared between parents, so a bare child code cannot be
# attributed back.
#
# funding_subarea (19 -> 1)
#     The second Förderbereich level. 91% of programmes carry one, but no single
#     source column exceeds 18% fill, so as 19 columns it was 28% of the table's
#     width and 43597 empty cells in a CSV. 11 sub-area codes occur under more
#     than one parent -- "beratung_schulung" under five -- and 56% of programmes
#     list several Förderbereiche, so dropping the parent would collapse 349 of
#     5963 values.
#
# applicant_sector (2 -> 1)
#     The applicant's economic sector. The two source columns carry the
#     byte-identical eight-sector list and differ only in which applicant type it
#     describes, so their names encode a value of another vocabulary
#     (foerderberechtigte) exactly as the uf_* names encode a Förderbereich. The
#     distinction is real data, not redundancy: "Niederlassung von Ärztinnen und
#     Ärzten" is freie_berufe for a founder and dienstleistungen for an existing
#     business, and InvestEU lists all eight sectors for founders against one for
#     companies. 15 of the 206 programmes that fill both differ that way.
SEPARATOR = "."  # SLUG forbids "/", and no category code contains a dot

PIVOTS: dict[str, dict[str, str]] = {
    "funding_subarea": {
        "uf_arbeit": "arbeit",
        "uf_aus_weiterbildung": "aus_weiterbildung",
        "uf_aussenwirtschaft": "aussenwirtschaft",
        "uf_beratung": "beratung",
        "uf_energieeffizienz": "energieeffizienz_erneuerbare_energien",
        "uf_existenzgruendung": "existenzgruendung_festigung",
        # The two Forschung classifier names are abbreviated upstream.
        "uf_forschung_offen": "forschung_innovation_themenoffen",
        "uf_forschung_spezifisch": "forschung_innovation_themenspezifisch",
        "uf_frauenfoerderung": "frauenfoerderung",
        "uf_gesundheit_soziales": "gesundheit_soziales",
        "uf_infrastruktur": "infrastruktur",
        "uf_kultur_medien_sport": "kultur_medien_sport",
        "uf_landwirtschaft": "landwirtschaft_laendliche_entwicklung",
        "uf_messen_ausstellungen": "messen_ausstellungen",
        "uf_regionalfoerderung": "regionalfoerderung",
        "uf_staedtebau_stadterneuerung": "staedtebau_stadterneuerung",
        "uf_umwelt_naturschutz": "umwelt_naturschutz",
        "uf_unternehmensfinanzierung": "unternehmensfinanzierung",
        "uf_wohnungsbau": "wohnungsbau_modernisierung",
    },
    "applicant_sector": {
        "branchen_existenzgruenderin": "existenzgruenderin",
        "branchen_unternehmen": "unternehmen",
    },
}

# Target column -> the vocabulary its parents come from, so a consumer can tell
# what the left half of a path is.
PIVOT_PARENT_VOCAB = {
    "funding_subarea": "foerderbereich",
    "applicant_sector": "foerderberechtigte",
}

# --- What a value has to look like -------------------------------------------

# A leaf label, e.g. "keine_grw_foerderung" or "gesellschaft fuer ... (giz)-31032".
# Deliberately loose: the point is that the "target:/BMWI/..." prefix is gone,
# and upstream slugs contain mixed case, spaces, parentheses and umlauts.
SLUG = r"^[^/]+$"

# Open-ended link lists: new entries appear constantly, so validate the shape
# of the leaf rather than its membership.
OPEN_LINK_FIELDS = ("funding_organisation", "contact_ids")

# The free-text columns, checked for stray markup rather than against a vocabulary.
TEXT_FIELDS = (
    "title", "description", "short_description", "legal_basis", "legal_citation",
    "legal_requirements", "procedure", "deadlines", "processing_time",
    "required_documents", "status_note", "seo_description",
    "application_language", "keywords",
)

# Bounds for the history timestamps, which are load times rather than anything the
# export states. Nothing can predate the first load, and a value in the far future
# means a clock or a timezone went wrong. Deliberately not "not after now": that
# would make the check depend on when it runs.
LOAD_EPOCH = datetime(2025, 1, 1, tzinfo=timezone.utc)
FAR_FUTURE = datetime(2100, 1, 1, tzinfo=timezone.utc)

# The earliest plausible date_of_issue, which is a date the source states rather
# than a load time, so it reaches back before this dataset exists. Programmes citing
# a Richtlinie from the 1990s would be plausible; none does, and a value before 2000
# has so far only ever been a parse error.
ISSUE_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)

# --- The inferred column ------------------------------------------------------
# ``keywords`` arrives as one string holding several keywords with no reliable
# separator -- see METRICS above -- and a multi-word keyword is indistinguishable
# from several one-word ones without reading the German. ``keywords_extracted`` is
# that string split up by a fine-tuned encoder (services/keyword_segmenter). The raw
# column stays untouched beside it.
#
# It is the only published column no rule produces, which is why
# :data:`fdb_scraper.schema.ORIGIN` exists: everything else either restates the
# export or follows from it, and a consumer reading the CSV has no way to tell the
# difference by looking.
INFERRED_COLUMNS: tuple[str, ...] = ("keywords_extracted",)

# Punctuation the segmenter strips from the edges of a term. Stated here rather
# than imported from ``segment.tokens``: services/keyword_segmenter is not an
# installed package, and this is the contract the published column is held to
# whatever produced it.
TERM_EDGE = " ,;.:()[]\"'"


# =============================================================================
# Which codelists are linked
# =============================================================================
#
# Published column -> (codelist, key into the export's closed vocabularies). The
# choice of codelist, not the per-code mapping: which codes correspond is derived
# by label match in :mod:`fdb_scraper.codelists`, and the exceptions that match
# cannot make live there beside it.
LINKED = {
    "funding_type": ("finanzierungsform", "foerderart"),
    "funding_body": ("geldgebende-institution", "foerdergeber"),
    "funding_location": ("nuts", "foerdergebiet"),
    "funding_area": ("foerderbereich", "foerderbereich"),
    "eligible_applicants": ("foerdernehmende", "foerderberechtigte"),
}

# Codelist code -> its URI, where the codelist publishes per-code URIs. XÖV
# identifies its lists by URN and gives codes no URI of their own, so only NUTS
# has one; for the rest a consumer resolves the code against the list identifier.
CODE_URI = {"nuts": "http://data.europa.eu/nuts/code/{}"}
