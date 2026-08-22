# fdb-scraper

Downloads the [Förderdatenbank](https://www.foerderdatenbank.de) programme export
(XML) regularily and transforms it into a tabular format. The data is published as a CSV file.

Replaces the
previous [scaper](https://github.com/CorrelAid/cdl_funding_scraper) we provided, as the upstream service added an xml export endpoint that is a bit more structured than html. Furthermore, the page now detects bots, leaving the xml endpoint as the only method for programmatic access

The xml schema of the export is rather chaotic and we did not include some fields that had no values or no useable values in the final dataset. Some fields still require processing or information extraction to be usable, but this was out of scope for this project. Feel free to open a PR.

## Data access

| What | URL |
| --- | --- |
| The data | `https://fdb.cdl.correlaid.org/data/programme.csv` |
| Column contract (CSVW) | `https://fdb.cdl.correlaid.org/table-schema.json` |
| Metadata (DCAT-AP.de) | `https://fdb.cdl.correlaid.org/id/dataset/foerderdatenbank-programme.ttl` |
| Minted vocabulary | `https://fdb.cdl.correlaid.org/def/fdb` |

Nine columns take their values from a closed vocabulary, enumerated in
[`fdb_scraper.generated.vocab`](src/fdb_scraper/generated/vocab.py) and enforced per
cell by the pandera schema — an unknown code fails the build rather than being
published. The table schema names the vocabulary and its size per column.

Five of those nine also have a loose alignment to a published codelist (XÖV
`finanzierungsform`, `geldgebende-institution`, `foerderbereich`, `foerdernehmende`,
and NUTS for `funding_location`), derived by label match in
[`fdb_scraper.codelists`](src/fdb_scraper/codelists.py). It is not published as part
of the dataset: the values stay the export's own codes, and the mapping is available
in-repo via `fdb_scraper.matches()` and `fdb_scraper.unmatched()` for anyone who wants
the standard code. 

See `notebooks/exploration.ipynb` for an example on how to load and use the data.

The column contract above is the reference for what every column means and what is
checked. The same table renders locally:

```python
from fdb_scraper.schema import describe
print(describe())          # dtype, nullability, checks and origin per column
```

`fdb:origin` marks each column `upstream`, `derived` or `inferred`, which is the one
thing the values cannot tell you themselves.

### History columns are ISO weeks

The export ships only its current state, so this dataset adds five columns of its
own: `on_website_from`, `last_updated`, `previous_update_dates`, `on_website_to` and
`absent`.

The four dates are **ISO weeks** (`2026-W34`), not timestamps. They are observed by
comparing one scheduled load with the one before it, so the week between two loads is
the resolution we actually have — a timestamp would claim a precision the source
never states. The week named is the interval a change fell in, not the load that
spotted it: a Monday load reports what changed during the week before it.

```
on_website_from        2026-W32
last_updated           2026-W34
previous_update_dates  ["2026-W33","2026-W34"]
on_website_to          null
absent                 false
```

Two consequences worth knowing before using them:

- `len(previous_update_dates)` is the number of *weeks* in which a change was seen,
  not the number of changes. One week is a single comparison, so two changes inside
  it are indistinguishable from one.
- `on_website_from == 2026-W32` means "present at the first load", not "appeared that
  week" — that load was a backfill of everything then on the site, and nothing before
  it is visible.

Only the scheduled weekly loads are used, so an extra manual scrape never changes
what is published. See [Scheduling](#scheduling).

> **Changed in [3b861b4](../../commit/3b861b4).** These four columns previously
> shipped microsecond timestamps (`2026-08-17T03:00:21.750769+0000`), and `absent`
> was called `deleted` with no `on_website_to`. Code that parses them as dates needs
> updating.

## Pipeline


| Step | Module | Description | 
| --- | --- | --- | 
| download + unzip | `scraper.export` | Loads documents to disk|
| structural contract | `contract.check_export` | raises `ContractError` on drift | 
| parse programmes | `parser.parse_programmes` | XML documents to rows; `scraper.scrape` is this with a download in front | 
| index linked documents | `links.resolve` |  contacts / addresses / links | 
| process | `process.process` | decoding, adding ids and links, collapsing pivots, renaming | 
| validate | `schema.build_schema` | raises `SchemaErrors` on bad values |

We additionally do some non-determinstic segmenting for keywords with a finetuned BERT model. In the export, keywords often (87.7%) carry no separator. The keywords are joined by single spaces, so a multi-word keyword is indistinguishable from several one-word keywords. The resulting field is called `keywords_extracted`.

In the future we might add more information extraction to this pipeline, but non-deterministic methods will always be declared as such. It would for example make sense to extract deadlines and provide them in a structured way. Currently, the deadline field is mostly not filled and not very structured.

## Scheduling

The load runs weekly, as a Coolify Scheduled Task: `0 3 * * 1` — Monday 03:00
**UTC**. Coolify's containers run UTC, so this does not follow the host's
`Europe/Berlin` clock (it fires at 05:00 local in summer) and does not shift with
DST.

That schedule is not only operational. Published dates are ISO weeks derived
from the loads matching this slot, so the cadence is part of the data contract
and is written down in three places:

| Where | What |
| --- | --- |
| Coolify → resource → Scheduled Tasks | the authority: what actually runs |
| [docker-compose.yaml](docker-compose.yaml) | documents it beside the service |
| `REGULAR_WEEKDAY` / `REGULAR_HOUR` in [src/fdb_scraper/history.py](src/fdb_scraper/history.py) | what the publish step matches loads against |

**Changing the schedule means changing all three.** If they drift apart, loads
stop matching the slot and every date in the dataset is relabelled. A publish
prints `no load at the scheduled slot` for any week it had to fall back on; more
than the occasional one means the constants are stale.

Manual runs are fine and do not corrupt anything — they are simply ignored when
the history is derived, so that every published week means the same thing.

## Parsing the XML yourself

If you want the XML step, copy it. It lives on its own in [src/fdb_scraper/parser.py](src/fdb_scraper/parser.py) and needs nothing but `polars` and the standard library. `parse_programmes("data/foerderprogramme_export")` is the whole entry point: give it any directory holding a `BMWI` tree and it returns the same 65-column frame.

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

## Scripts

| # | Script | Description | Input it needs | Regenerate when |
| --- | --- | --- | --- | --- |
| 1 | `gen_codelist_data.py` | The published codelists — xflb from XRepository, NUTS from Eurostat — into `generated/codelist_data.py` | network | a registry relabels or adds a code |
| 2 | `gen_vocab.py` | The export's own label bundle, which the closed vocabularies are matched against, into `generated/vocab.py` | an extracted export | upstream labels changed. **After 1**: `process.decode` reads the codelist data |
| — | `gen_contract.py` | Which properties each document type carries and what container each declares, into `generated/contract_data.py` | an extracted export | a `ContractError` turned out to be a legitimate upstream change. Independent of the rest |
| 3 | `build_dist.py` | The pipeline itself: load, process, validate, write the CSV and the metadata into a staging tree | — | every run; it produces the CSV that 4 measures |
| 4 | `gen_dcat.py` | The committed `dcat/` — dataset document, minted vocabulary, CSVW table schema, each also as JSON-LD and HTML. A command line around `fdb_scraper.dcat`, which is what the pipeline and the tests import | `schema`/`semantics` + that CSV | a published column, URI or vocabulary changed |