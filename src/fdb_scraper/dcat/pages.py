"""HTML landing pages for the URIs the RDF documents answer for.

The graphs in :mod:`fdb_scraper.dcat.graphs` are what machines read; these pages
are what a human gets from the same URI in a browser. Same content, same URIs,
different serialisation -- each renderer reads the graph it accompanies, so a
change to the graph is the only thing that has to stay in sync.

Stdlib templating only: the project keeps dependencies tight (see pyproject), and
the markup is small enough that f-strings beat a templating dependency.
"""

from __future__ import annotations

import html

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from fdb_scraper.config import DATASET, VOCAB
from fdb_scraper.dcat.profile import DCAT, DCATAPDE, DCT, FDB, FOAF, PROV

_HTML_HEAD = """\
<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="alternate" type="text/turtle" href="{href_root}{href_ext}">
<link rel="alternate" type="application/ld+json" href="{href_root}{href_ext_jsonld}">
<style>
  /* Colours, fonts and spacing are the CorrelAid CDL design tokens
     (@correlaid/cdl-design). Copied as literals rather than imported: this page
     is a static file served next to the RDF, with no build step and no request
     to a stylesheet the site would then have to keep serving. The brand faces
     are licensed and not hosted here, so each stack falls back on its own. */
  :root {{
    --bg: #8bb8ff; --fg: #86185f; --accent: #86184f; --surface: #ffffff;
    --sans: "Inter", -apple-system, "Segoe UI", sans-serif;
    --heading: "PP Fraktion Sans", -apple-system, "Segoe UI", sans-serif;
    --mono: "PP Fraktion Mono", ui-monospace, "Source Code Pro", monospace;
  }}
  body {{ font-family: var(--sans); background: var(--bg); color: var(--fg);
    max-width: 42rem; margin: 0 auto; padding: 2rem 1rem; line-height: 1.6; }}
  h1, h2 {{ font-family: var(--heading); letter-spacing: -0.01em; line-height: 1.15; }}
  h1 {{ font-size: 1.9rem; }}
  h2 {{ font-size: 1.4rem; margin-top: 2rem; }}
  dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 0.35rem 1.5rem; }}
  dt {{ font-weight: 700; }}
  dd {{ margin: 0; }}
  .desc {{ white-space: pre-wrap; }}
  code {{ font-family: var(--mono); background: var(--surface); padding: 0.1em 0.3em;
    border-radius: 0.25rem; }}
  a {{ color: var(--accent); text-decoration-thickness: 1.5px; }}
  hr {{ border: 0; border-top: 1.5px solid var(--accent); margin: 2rem 0; }}
  table {{ border-collapse: collapse; }}
  th, td {{ text-align: left; padding: 0.4rem 0.8rem 0.4rem 0; vertical-align: top; }}
</style>
</head>
<body>
"""


_HTML_FOOT = """\
<hr>
<p><small>
Representations: <a href="{href_root}">Turtle</a> &middot;
<a href="{href_root}{ext_jsonld}">JSON-LD</a> &middot;
<a href="{href_root}{ext_turtle}">.ttl</a>.
</small></p>
</body>
</html>
"""


def _lit(graph: Graph, subject: URIRef, predicate: URIRef) -> str | None:
    """First literal value of ``(subject, predicate)`` in ``graph``, or None.

    HTML-escaped. Used by the page renderers to pull DCAT metadata into the
    landing pages without re-typing it in two places.
    """
    for obj in graph.objects(subject, predicate):
        if isinstance(obj, Literal):
            return html.escape(str(obj))
    return None


def _lit_list(graph: Graph, subject: URIRef, predicate: URIRef) -> list[str]:
    """All literal values of ``(subject, predicate)``, HTML-escaped."""
    return [html.escape(str(o)) for o in graph.objects(subject, predicate) if isinstance(o, Literal)]


def _iri(graph: Graph, subject: URIRef, predicate: URIRef) -> str | None:
    """First URI value of ``(subject, predicate)``, or None. HTML-escaped."""
    for obj in graph.objects(subject, predicate):
        if isinstance(obj, URIRef):
            return html.escape(str(obj))
    return None


def render_dataset_html(graph: Graph) -> str:
    """HTML landing page for the dataset URI -- same graph as the .ttl output.

    The page mirrors the DCAT body in plain text: title, bilingual description,
    publisher, license, distribution. No decoration that a stylesheet would
    change later; no navigation chrome that has to be kept in sync with the
    site; just the resource, described for a human who landed on its URI.
    """
    dataset = URIRef(DATASET)
    title = _lit(graph, dataset, DCT.title) or DATASET
    descriptions = _lit_list(graph, dataset, DCT.description)
    keywords = _lit_list(graph, dataset, DCAT.keyword)
    themes = [html.escape(str(o)) for o in graph.objects(dataset, DCAT.theme)]
    landing = _iri(graph, dataset, DCAT.landingPage)
    # The licence hangs off the distribution, not the dataset: the table is what is
    # licensed. Read from wherever it was asserted rather than guessed at, so the
    # row renders instead of silently disappearing.
    license_iri = next(
        (
            _iri(graph, dist, DCT.license)
            for dist in graph.objects(dataset, DCAT.distribution)
            if _iri(graph, dist, DCT.license)
        ),
        None,
    )
    modified = _lit(graph, dataset, DCT.modified)
    conformsto = _iri(graph, dataset, DCT.conformsTo)

    # Build the distribution table. There is one today (CSV); the loop is the
    # right shape for the day a JSON distribution lands.
    dist_rows = []
    for dist in graph.objects(dataset, DCAT.distribution):
        d_title = _lit(graph, dist, DCT.title) or ""
        d_url = _iri(graph, dist, DCAT.downloadURL) or _iri(graph, dist, DCAT.accessURL) or ""
        d_format = _iri(graph, dist, DCT.format) or ""
        d_size = _lit(graph, dist, DCAT.byteSize)
        dist_rows.append(
            f"<tr><td>{d_title}</td><td>{d_format}</td>"
            f"<td>{('{:,} bytes'.format(int(d_size)) if d_size else '')}</td>"
            f"<td><a href={html.escape(d_url, quote=True)}>{html.escape(d_url)}</a></td></tr>"
        )

    desc_html = "".join(f"<div class=\"desc\">{d}</div>" for d in descriptions)
    kw_html = ", ".join(f"<code>{k}</code>" for k in keywords)
    themes_html = ", ".join(f"<code>{t.rsplit('/', 1)[-1]}</code>" for t in themes)

    return (
        _HTML_HEAD.format(lang="de", title=title, href_root=DATASET, href_ext="", href_ext_jsonld=".jsonld")
        + f"<h1>{title}</h1>\n"
        + f"<div class=\"desc\">{desc_html}</div>\n"
        + (f"<h2>Distributions</h2>\n<table><thead><tr><th>Title</th><th>Format</th><th>Size</th><th>Download</th></tr></thead>"
           f"<tbody>{''.join(dist_rows)}</tbody></table>\n" if dist_rows else "")
        + "<h2>Metadata</h2>\n"
        + "<dl>"
        + (f"<dt>Modified</dt><dd>{modified}</dd>" if modified else "")
        + (f"<dt>License</dt><dd><code>{license_iri}</code></dd>" if license_iri else "")
        + (f"<dt>Conforms to</dt><dd><a href={html.escape(conformsto, quote=True)}>{html.escape(conformsto)}</a></dd>"
           if conformsto else "")
        + (f"<dt>Landing page</dt><dd><a href={html.escape(landing, quote=True)}>{html.escape(landing)}</a></dd>"
           if landing else "")
        + (f"<dt>Themes</dt><dd>{themes_html}</dd>" if themes_html else "")
        + (f"<dt>Keywords</dt><dd>{kw_html}</dd>" if kw_html else "")
        + "</dl>\n"
        + _HTML_FOOT.format(href_root=DATASET, ext_turtle=".ttl", ext_jsonld=".jsonld")
    )


def render_vocabulary_html(graph: Graph) -> str:
    """HTML page for the vocabulary URI: one row per minted term.

    Rendered as a table so a human can scan what is in the namespace. The
    ``fdb:`` prefix is implicit (this document defines it) and is dropped from
    the displayed identifiers.
    """
    ontology = URIRef(VOCAB.rstrip("#"))
    title = _lit(graph, ontology, DCT.title) or VOCAB
    description = _lit(graph, ontology, DCT.description) or ""

    rows = []
    for term, _, _ in graph.triples((None, RDF.type, RDF.Property)):
        label = _lit(graph, term, RDFS.label) or term.rsplit("#", 1)[-1]
        comment = _lit(graph, term, RDFS.comment) or ""
        origin = _lit(graph, term, FDB.origin) or ""
        rows.append(
            f"<tr><td><code>{label}</code></td><td>{comment}</td><td><code>{origin}</code></td></tr>"
        )

    class_rows = []
    for cls, _, _ in graph.triples((None, RDF.type, RDFS.Class)):
        label = _lit(graph, cls, RDFS.label) or cls.rsplit("#", 1)[-1]
        comment = _lit(graph, cls, RDFS.comment) or ""
        class_rows.append(f"<tr><td><code>{label}</code></td><td>{comment}</td></tr>")

    return (
        _HTML_HEAD.format(lang="de", title=title, href_root=VOCAB.rstrip("#"), href_ext="", href_ext_jsonld=".jsonld")
        + f"<h1>{title}</h1>\n"
        + (f"<div class=\"desc\">{description}</div>\n" if description else "")
        + ("<h2>Classes</h2>\n<table><thead><tr><th>Term</th><th>Comment</th></tr></thead>"
           f"<tbody>{''.join(class_rows)}</tbody></table>\n" if class_rows else "")
        + ("<h2>Properties</h2>\n<table><thead><tr><th>Term</th><th>Comment</th><th>Origin</th></tr></thead>"
           f"<tbody>{''.join(rows)}</tbody></table>\n" if rows else "")
        + _HTML_FOOT.format(href_root=VOCAB.rstrip("#"), ext_turtle=".ttl", ext_jsonld=".jsonld")
    )


def render_index_html(graph: Graph) -> str:
    """Landing page at the root -- a short description and links into the data.

    Replaces the directory listing Caddy would otherwise serve. Same data as
    the dataset landing page, less detail: a person who landed on the host
    root wants to know what this is and where to start, not the full DCAT body.
    """
    dataset = URIRef(DATASET)
    title = _lit(graph, dataset, DCT.title) or "Förderdatenbank"
    descriptions = _lit_list(graph, dataset, DCT.description)
    # First paragraph only -- the long descriptions live on the dataset page.
    short_desc = ""
    for desc in descriptions:
        if "\n" in desc:
            short_desc = desc.split("\n", 1)[0]
            break

    # Both links come out of the graph rather than being written here: the download
    # URL and the schema URL are stated in fdb_scraper.uris and asserted on the
    # dataset, so a path typed here would be a third spelling of them.
    downloads = [
        _iri(graph, dist, DCAT.downloadURL)
        for dist in graph.objects(dataset, DCAT.distribution)
        if _iri(graph, dist, DCAT.downloadURL)
    ]
    schema_url = _iri(graph, dataset, DCT.conformsTo)

    # Provenance block. Every value is read back out of the graph rather than
    # imported from config, so the page cannot disagree with the RDF it sits next
    # to -- if a triple is dropped upstream, the row disappears with it.
    publisher = next(graph.objects(dataset, DCT.publisher), None)
    pub_name = _lit(graph, publisher, FOAF.name) if publisher else None
    pub_home = _iri(graph, publisher, FOAF.homepage) if publisher else None
    pub_mbox = _iri(graph, publisher, FOAF.mbox) if publisher else None
    source = _iri(graph, dataset, FOAF.page)
    derived = _iri(graph, dataset, PROV.wasDerivedFrom)
    frequency = _iri(graph, dataset, DCT.accrualPeriodicity)
    modified = _lit(graph, dataset, DCT.modified)
    # Licence and its attribution line live on the distribution: the table is
    # what is licensed, not the description of it.
    license_iri = attribution = None
    for dist in graph.objects(dataset, DCAT.distribution):
        license_iri = license_iri or _iri(graph, dist, DCT.license)
        attribution = attribution or _lit(graph, dist, DCATAPDE.licenseAttributionByText)

    def _link(url: str, text: str | None = None) -> str:
        return f'<a href="{url}">{text or url}</a>'

    rows = []
    if pub_name:
        who = _link(pub_home, pub_name) if pub_home else pub_name
        if pub_mbox:
            who += " &middot; " + _link(pub_mbox, pub_mbox.removeprefix("mailto:"))
        rows.append(("Published by", who))
    if source:
        where = _link(source, "Förderdatenbank des Bundes")
        if derived:
            where += f" ({_link(derived, 'export')})"
        rows.append(("Source", where))
    if license_iri:
        terms = f"<code>{license_iri}</code>"
        if attribution:
            terms += f"<br>Attribution: {attribution}"
        rows.append(("Licence", terms))
    if modified:
        when = modified
        if frequency:
            when += " &middot; " + frequency.rsplit("/", 1)[-1].lower()
        rows.append(("Updated", when))

    about = "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in rows)

    return (
        _HTML_HEAD.format(lang="de", title=title, href_root=DATASET, href_ext="", href_ext_jsonld=".jsonld")
        + f"<h1>{title}</h1>\n"
        + (f"<p>{short_desc}</p>\n" if short_desc else "")
        + "<h2>Get the data</h2>\n<ul>"
        + "".join(
            f"<li><a href=\"{url}\">{url.rsplit('/', 1)[-1]}</a> &mdash; the table "
            f"({html.escape(_lit(graph, dataset, DCT.modified) or '')})</li>"
            for url in downloads
        )
        + f"<li><a href=\"{DATASET}\">Dataset description</a> &mdash; DCAT body, also as <a href=\"{DATASET}.ttl\">Turtle</a> or <a href=\"{DATASET}.jsonld\">JSON-LD</a></li>"
        + (f"<li><a href=\"{schema_url}\">Table schema</a> &mdash; the per-column contract</li>" if schema_url else "")
        + f"<li><a href=\"{VOCAB.rstrip('#')}\">Vocabulary</a> &mdash; terms minted for columns with no foreign equivalent</li>"
        "</ul>\n"
        + (f"<h2>About</h2>\n<dl>{about}</dl>\n" if about else "")
        + _HTML_FOOT.format(href_root=DATASET, ext_turtle=".ttl", ext_jsonld=".jsonld")
    )
