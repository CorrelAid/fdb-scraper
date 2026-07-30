# fdb-scraper

Downloads the [Förderdatenbank](https://www.foerderdatenbank.de) programme export
(XML) regularily and transforms it into a tabular format. The data is published as a CSV file.

Replaces the
previous [scaper](https://github.com/CorrelAid/cdl_funding_scraper) we provided, as the upstream service added an xml export endpoint that is a bit more structured than html. Furthermore, the page now detects bots, leaving the xml endpoint as the only method for programmatic access

## Data access

| What | URL |
| --- | --- |
| The data | `https://fdb.correlaid.org/data/programme.csv` |
| Column contract (CSVW) | `https://fdb.correlaid.org/table-schema.json` |
| Metadata (DCAT-AP.de) | `https://fdb.correlaid.org/id/dataset/foerderdatenbank-programme.ttl` |
| Minted vocabulary | `https://fdb.correlaid.org/def/fdb` |

The metadata document is self-contained and is what a harvester fetches. This
repository publishes **no `dcat:Catalog`** — the catalogue listing this dataset
alongside the Civic Data Lab's others is a separate deployment that merges this
document with the others. See [CATALOGUE.md](CATALOGUE.md), which also specifies how
to build that aggregator.

## Pipeline


| Step | Module | Description | 
| --- | --- | --- | 
| download + unzip | `scraper.export` | Loads documents to disk|
| structural contract | `contract.check_export` | raises `ContractError` on drift | 
| parse programmes | `scraper.scrape` | extracts useable fields | 
| index linked documents | `links.resolve` |  contacts / addresses / links | 
| process | `process.process` | decoding, adding ids and links, collapsing pivots, renaming | 
| validate | `schema.build_schema` | raises `SchemaErrors` on bad values |




## Field names

The XML property names come from a generic CMS template and several mean the
opposite of what they say. Each mapping was confirmed against a rendered detail
page and all 2500 values.

| Page section | XML property | Published |
| --- | --- | --- |
| Kurzzusammenfassung › Kurztext | `gsb:teaserText` | `short_description` |
| Kurzzusammenfassung › Volltext | `gsb:summary` | `description` |
| Rechtsgrundlage › Richtlinie | `gsb:bodyText` | `legal_basis` |
| Rechtsgrundlage (citation) | `gsb:procDescription` | `legal_citation` |
| Zusatzinfos › Rechtliche Voraussetzungen | `gsb:regulatoryFWork` | `legal_requirements` |
| Zusatzinfos › Verfahrensablauf | `gsb:procMethod` | `procedure` |
| Zusatzinfos › Fristen | `gsb:procInfluence` | `deadlines` |
| Zusatzinfos › Bearbeitungsdauer | `gsb:progress` | `processing_time` |
| Zusatzinfos › Erforderliche Unterlagen | `gsb:competenceDescr` | `required_documents` |
| Antragssprache | `gsb:functions` | `application_language` |
| Suchmaschinen-Beschreibung | `gsb:remark` | `seo_description` |

Sub-sections stay apart, so the crawler's `more_info` blob is gone. Reasons for
every dropped field are in `schema.DROPPED_FIELDS`.



## Install

`uv sync` (add `--extra notebook` for the exploration notebook).

No configuration is needed to run locally: unset, the pipeline writes its history to a local DuckDB file. Every variable below is optional.

| Variable | Default | What it does |
| --- | --- | --- |
| `POSTGRES_CONN_STR` | unset | Store the scd2 history in Postgres instead of DuckDB. This is what the deployment sets |
| `FDB_DB` | `data/fdb.duckdb` | DuckDB file, used only while `POSTGRES_CONN_STR` is unset |
| `DLT_DATA_DIR` | dlt's own default | dlt's working directory. Must persist across runs for load ids and pipeline state to line up |
| `FDB_TAGGER_URL`, `FDB_TAGGER_TOKEN` | unset | The keyword segmenter endpoint. Both needed, or the load skips segmentation and `keywords_extracted` stays null |
| `FDB_KEYWORD_CACHE` | client default, relative to the working directory | The segmenter client's sqlite cache. The deployment moves it onto the state volume |
| `FDB_SEGMENTER_DIR` | `services/keyword_segmenter` beside the package | Where `history` imports the segmenter client from. Set by the image, which copies only that directory |
| `FDB_TEST_POSTGRES` | unset | A Postgres URL makes the twelve history tests run against Postgres instead of skipping |
| `NOTIFY_TO` | unset | Comma-separated recipients for mail on a failed run. Empty means `scripts/notify.py` does nothing, so the other five below are unused |
| `NOTIFY_FROM`, `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD` | unset | Required once `NOTIFY_TO` is set |
| `SMTP_PORT` | `587` | STARTTLS |

The two tagger variables have a local home: `.env.tagger` at the repo root,
gitignored, sourced with `set -a; . ./.env.tagger; set +a`.
[notebooks/model_demos.ipynb](notebooks/model_demos.ipynb) reads it directly and
demonstrates calling the endpoint.

The published URLs are *not* configurable; they are hardcoded in
[src/fdb_scraper/uris.py](src/fdb_scraper/uris.py) on purpose.

## Scripts

| # | Script | Description | Input it needs | Regenerate when |
| --- | --- | --- | --- | --- |
| 1 | `gen_codelist_data.py` | The published codelists — xflb from XRepository, NUTS from Eurostat — into `generated/codelist_data.py` | network | a registry relabels or adds a code |
| 2 | `gen_vocab.py` | The export's own label bundle, which the closed vocabularies are matched against, into `generated/vocab.py` | an extracted export | upstream labels changed. **After 1**: `process.decode` reads the codelist data |
| — | `gen_contract.py` | Which properties each document type carries and what container each declares, into `generated/contract_data.py` | an extracted export | a `ContractError` turned out to be a legitimate upstream change. Independent of the rest |
| 3 | `build_dist.py` | The pipeline itself: load, process, validate, write the CSV and the metadata into a staging tree | — | every run; it produces the CSV that 4 measures |
| 4 | `gen_dcat.py` | The committed `dcat/` — dataset document, minted vocabulary, CSVW table schema | `schema`/`semantics` + that CSV | a published column, URI or vocabulary changed |