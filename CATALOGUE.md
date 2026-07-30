# The catalogue is somewhere else

This repository publishes **one dataset** and no `dcat:Catalog`. The catalogue that
lists it alongside the Civic Data Lab's other datasets is a separate deployment —
call it the aggregator — which fetches this repository's dataset document and merges
it with the others.

What this repository guarantees, and all it guarantees:

```
https://fdb.correlaid.org/id/dataset/foerderdatenbank-programme.ttl
```

A single self-contained Turtle document: the `dcat:Dataset`, its
`dcat:Distribution`, the publisher `foaf:Agent` and the `vcard:Kind` contact point,
all in one graph, DCAT-AP.de 2.0 valid on its own
(`tests/test_dcat.py::test_the_dataset_document_is_self_contained`).

Self-containment is the whole contract, and it is not decoration. Piveau's
`importing-rdf` and CKAN's `ckanext-dcat` both parse the one document they fetch and
take the `dcat:Dataset` subjects out of that graph. **Neither dereferences a URI to
collect properties from a second document.** A catalogue that merely links to
datasets harvests as a set of datasets with no title, no distribution and no
licence.

The extension is in the URL on purpose. `/id/dataset/foerderdatenbank-programme`
content-negotiates and returns Turtle only to a client that asks for it; the `.ttl`
path always returns Turtle, and a harvester sending no `Accept` header is normal.

## Why the catalogue is not here

A `dcat:Catalog` node in this repository would be a claim about what the Civic Data
Lab publishes as a whole, made by a repository that knows about one dataset. Two
such claims — one here, one in the aggregator — is how a portal ends up harvesting
the same dataset twice under two parents. `test_no_catalogue_is_published` keeps it
from creeping back.

---

# Building the aggregator

## 0. What it is

One small repository, one host, one served file. No database, no queue, no CKAN. The
whole thing is a scheduled `rdflib` merge behind a static file server — the same
shape as this repository's `files` service, and deployable the same way (see
`DEPLOY.md`).

Suggested host: `catalog.correlaid.org`. Not a path under `correlaid.org`: that
would make the catalogue depend on a proxy rule in the website repository, the same
reasoning that put this dataset on its own subdomain.

## 1. What it serves

| URL | What |
| --- | --- |
| `/catalogue.ttl` | The merged catalogue. **This is the harvest URL** handed to piveau, GovData, anyone. |
| `/catalogue.jsonld` | Same graph, JSON-LD. Optional, cheap — one extra `serialize()`. |
| `/` | An HTML index listing the datasets. Optional, but it is what a human who follows the harvest URL expects to find. |

Nothing else. The aggregator owns no data and no dataset identifiers.

## 2. The catalogue node

One hand-written file, versioned in the aggregator repository, edited by a human.
It is the only metadata the aggregator authors:

```turtle
PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX dct:  <http://purl.org/dc/terms/>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>

<https://catalog.correlaid.org/catalogue.ttl#catalog>
    a dcat:Catalog ;
    dct:title "Civic Data Lab -- Datenkatalog"@de ;
    dct:description "Datensätze, die das Civic Data Lab (CorrelAid e.V.) veröffentlicht."@de ;
    dct:publisher <https://catalog.correlaid.org/id/agent/correlaid> ;
    dct:language <http://publications.europa.eu/resource/authority/language/DEU> ;
    dct:license <http://dcat-ap.de/def/licenses/cc-by/4.0> ;
    dcat:themeTaxonomy <http://publications.europa.eu/resource/authority/data-theme> ;
    foaf:homepage <https://correlaid.org/> ;
.
```

`dcat:themeTaxonomy` because DCAT-AP wants it on the catalogue and no dataset can
supply it. `dct:modified` is **not** here — the build stamps it (step 4).

The publisher agent: reuse `https://fdb.correlaid.org/id/agent/correlaid` and let
this repository's dataset document describe it, or mint a host-neutral agent URI in
the aggregator and describe it in the catalogue node. Either works; do not do both,
or two `foaf:Agent`s with different URIs both claim to be CorrelAid.

## 3. The source list

```yaml
# sources.yaml
sources:
  - name: foerderdatenbank-programme
    url: https://fdb.correlaid.org/id/dataset/foerderdatenbank-programme.ttl
  # one entry per dataset, added by hand
```

Explicit and hand-maintained. Not discovered, not crawled: a catalogue that
enumerates itself from something else is a catalogue whose contents nobody
approved.

## 4. The build

```python
# build.py -- run on a schedule, writes public/catalogue.ttl
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCAT, DCTERMS, RDF, XSD

CATALOG = URIRef("https://catalog.correlaid.org/catalogue.ttl#catalog")
OUT = Path("public/catalogue.ttl")
CACHE = Path("cache")  # last good copy of every source

g = Graph()
g.parse("catalogue-node.ttl", format="turtle")

failed, seen = [], set()
for source in yaml.safe_load(Path("sources.yaml").read_text())["sources"]:
    cached = CACHE / f"{source['name']}.ttl"
    try:
        body = httpx.get(source["url"], timeout=30, follow_redirects=True).raise_for_status().text
        d = Graph().parse(data=body, format="turtle")   # parse before caching
        cached.write_text(body)
    except Exception as e:                               # noqa: BLE001 -- any failure falls back
        if not cached.is_file():
            print(f"{source['name']}: unreachable and never cached: {e}", file=sys.stderr)
            failed.append(source["name"])
            continue
        print(f"{source['name']}: {e} -- serving the cached copy", file=sys.stderr)
        failed.append(source["name"])
        d = Graph().parse(cached, format="turtle")

    datasets = list(d.subjects(RDF.type, DCAT.Dataset))
    if not datasets:
        print(f"{source['name']}: no dcat:Dataset in the document", file=sys.stderr)
        failed.append(source["name"])
        continue

    g += d                                    # absolute URIs: no rewriting, no collisions
    for ds in datasets:
        g.add((CATALOG, DCAT.dataset, ds))
        seen.add(ds)

# The catalogue changed when any dataset in it changed. Harvesters that compare
# dct:modified skip a run otherwise, so a stale stamp is a silently frozen catalogue.
latest = max(
    (o.toPython() for _, o in g.subject_objects(DCTERMS.modified) if hasattr(o, "toPython")),
    default=datetime.now(timezone.utc),
)
g.add((CATALOG, DCTERMS.modified, Literal(getattr(latest, "date", lambda: latest)(), datatype=XSD.date)))

OUT.parent.mkdir(parents=True, exist_ok=True)
staged = OUT.with_suffix(".ttl.incoming")     # atomic: never serve a half-written catalogue
staged.write_bytes(g.serialize(format="longturtle", encoding="utf-8"))
staged.replace(OUT)
print(f"{OUT}: {len(seen)} datasets, {len(g)} triples" + (f", degraded: {failed}" if failed else ""))
raise SystemExit(1 if failed else 0)
```

Four properties of this that are not incidental:

**Merge, never rewrite.** Every URI in a source document is absolute and already
dereferences at its own host. Rewriting them into the aggregator's namespace would
mint a second identifier for the same dataset and break the link between the
catalogue entry and the document that describes it.

**A source that fails keeps its last good copy.** This is the one that bites. If a
dataset silently drops out of the catalogue, piveau's exporter sees it missing from
the run's identifier list and **deletes it from the portal** — one bad deploy on one
source host, and a dataset that took a year to build disappears from the Datenatlas.
Serving the cached copy keeps it listed with slightly stale metadata, which is
strictly better. Exit non-zero so the failure is loud without being destructive.

**Parse before caching.** An HTML error page served with `200` parses as nothing;
writing it to the cache would poison the fallback for every later run.

**Stamp `dct:modified` from the contents.** A frozen date makes conditional
harvesters skip forever.

## 5. Validate before publishing

Same gate as this repository, against the same vendored shapes
(`tests/fixtures/shapes/`, DCAT-AP.de 2.0 Spezifikation):

```python
conforms, results, text = validate(g, shacl_graph=shapes, advanced=True, inference="none")
```

The catalogue shapes now have a focus node, so this checks what nothing checks
today: `dct:title`, `dct:description`, `dct:publisher`, `dcat:themeTaxonomy` on the
catalogue, and that every `dcat:dataset` object is a described `dcat:Dataset`.

Exclude `skos:inScheme` results for focus nodes outside your own namespaces, for
the reason spelled out in `tests/test_dcat.py` — offline they cannot be decided.
Before a first publish, put the merged file through
<https://www.itb.ec.europa.eu/shacl/dcat-ap.de/upload>, which resolves the remote
vocabularies and does decide them.

## 6. Deploy

Mirror `DEPLOY.md`: a Caddy container serving `public/` on `:80`, `catalog.correlaid.org`
attached to it, and a scheduled task running `build.py`. Caddy needs the Turtle
content type, which Go's mime package does not know:

```
@ttl path *.ttl
header @ttl Content-Type "text/turtle; charset=utf-8"
header Access-Control-Allow-Origin "*"
```

Schedule it more often than the fastest source — daily against this repository's
weekly pipeline. The build is a few HTTP GETs and an in-memory merge; there is no
reason to be frugal.

## 7. Register with the harvester

Piveau, once, per `doc.piveau.eu`:

```sh
curl -i -X PUT https://<hub>/catalogues/correlaid \
  -H "X-API-Key: $KEY" -H "Content-Type: text/turtle" \
  --data-binary @catalogue-node.ttl
```

then a pipe whose `importing-rdf` config is:

```json
{
  "address": "https://catalog.correlaid.org/catalogue.ttl",
  "catalogue": "correlaid",
  "inputFormat": "text/turtle",
  "alternativeLoad": false
}
```

**One pipe per catalogue, never two.** Piveau scopes deletion to the target
catalogue: a second pipe importing a different source into `correlaid` deletes the
first pipe's datasets on every run. That hazard is exactly why the merge happens in
the aggregator instead of being pushed into piveau as several pipes.

## 8. Adding a dataset later

1. The new project serves a self-contained dataset document at a stable URL — its
   own `dcat:Dataset`, distributions, publisher, contact, no `dcat:Catalog`.
2. Add one entry to `sources.yaml`.
3. Run the build; SHACL must pass; the dataset count must go up by one.

No downstream configuration changes. Nobody re-registers a pipe, nobody edits a
harvester. That is the property one shared catalogue buys, and the reason to prefer
it over a catalogue per project.
