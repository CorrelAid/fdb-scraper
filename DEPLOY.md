# Deploying on Coolify

Three services on one host: a Postgres holding the change history, a pipeline
container that runs weekly, and Caddy serving the result. Coolify's server-level
proxy terminates TLS and routes by Host, so nothing here manages certificates.

Everything below has been verified end to end against a real Coolify-shaped stack
except the two steps that need your Coolify instance — the Backups tab and the
Scheduled Task. Those are marked.

## 0. The domain is already decided: `fdb.correlaid.org`

Nothing to configure here — this section is what you have to *match*. Every
published URL is stated once, in `src/fdb_scraper/uris.py`:

```python
HOST            = "https://fdb.correlaid.org"
BASE            = f"{HOST}/id/"                        # identifier
VOCAB           = f"{HOST}/def/fdb#"                   # identifier
DATASET         = f"{BASE}dataset/{DATASET_ID}"        # identifier
DATASET_DOC_URL = f"{DATASET}.ttl"                     # location
SCHEMA_URL      = f"{HOST}/table-schema.json"
DOWNLOAD_BASE   = f"{HOST}/data/"
```

So **attach `fdb.correlaid.org` in step 4**, not some other name. The dataset
document tells harvesters to fetch `https://fdb.correlaid.org/data/programme.csv`; if
the stack answers on a different host, the harvest 404s and the identifiers do not
dereference.

`DATASET_DOC_URL` is the harvesting interface — a self-contained DCAT-AP.de
description of the one dataset. This stack serves no `dcat:Catalog`; the aggregating
catalogue that lists this dataset fetches that URL. See `CATALOGUE.md`, and give that
URL to whoever configures the aggregator.

Hardcoded rather than read from an environment variable, deliberately. `dcat/` is
generated *and committed*, so an env var would make the reviewable artefact depend on
whoever last ran the generator, and the tests spell the URIs out so a changed host
fails instead of passing silently. A wrong URL here is not a runtime error — it is a
published mistake that consumers cache.

A subdomain rather than the apex because `correlaid.org` is the main website:
routing `/id/` and `/def/` through it would make every identifier here depend on a
proxy rule in another repo.

If the host ever has to move anyway, it is one edit to `HOST` plus:

```sh
uv run python scripts/gen_dcat.py                 # regenerate dcat/
uv run pytest tests/test_dcat.py tests/test_build_dist.py
```

and updating the literals those tests assert. Expect to break every consumer that
cached the old identifiers — that is the intended friction.

## 1. Prerequisites

- A Coolify server with the proxy running.
- DNS: an A record for `fdb.correlaid.org` pointing at that server. Coolify issues the
  certificate once the domain is attached.
- The repo reachable by Coolify (GitHub App, deploy key, or a public clone URL).

## 2. Create the host directories

The pipeline container runs as uid 1000 and writes to a bind mount, so the
directories must exist and be owned by that uid. On the Coolify host:

```sh
sudo install -d -o 1000 -g 1000 /data/fdb /data/fdb/public /data/fdb/state
```

Two directories, and the split matters:

```
/data/fdb/public/   served by Caddy, read-only
/data/fdb/state/    dlt's working files — NEVER served
```

Caddy serves its root with directory listings on. Mounting `/data/fdb` instead of
`/data/fdb/public` would publish dlt's working directory, which contains extracted
copies of the export. `tests/test_compose.py::test_the_history_is_not_served` asserts
this, and it fails if the mount is widened.

## 3. Create the resource

Coolify → **New Resource** → **Docker Compose** → point at this repository.

| Setting | Value |
| --- | --- |
| Compose file | `docker-compose.yaml` |
| Base directory | `/` |
| Branch | whichever you publish from |

Then set the environment variables. One is required; the rest have working defaults:

| Variable | Value | Notes |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | generate one | **Required.** The compose fails to start without it rather than falling back to trust authentication |
| `POSTGRES_USER` | `fdb` | optional, defaults to `fdb` |
| `POSTGRES_DB` | `fdb` | optional, defaults to `fdb` |
| `FDB_DATA` | leave unset | defaults to `/data/fdb`; only the test suite overrides it |

Optionally, the keyword segmenter. Both must be set for it to run at all
(`fdb_scraper.history.tagger_configured`); without them the load still records the
export and only skips the segmentation, so `keywords_extracted` publishes as null for
whatever has no stored segmentation:

| Variable | Value | Notes |
| --- | --- | --- |
| `FDB_TAGGER_URL` | the Modal endpoint | see `services/keyword_segmenter/README.md` |
| `FDB_TAGGER_TOKEN` | its bearer token | mark it secret in Coolify |

`FDB_KEYWORD_CACHE` is set in the compose file to `/srv/state/keyword_cache.sqlite`.
It has to be on the state volume: the client's default path is relative to the image's
working directory, which the pipeline's user cannot write.

And optionally, mail on a failed run — a weekly job that fails silently is a dataset
that quietly stops updating, and nothing else here notices. `scripts/notify.py` is a
no-op while `NOTIFY_TO` is empty, so leaving the whole group unset is a supported
state — nothing below it is read until a recipient exists:

| Variable | Value | Notes |
| --- | --- | --- |
| `NOTIFY_TO` | `you@example.org` | comma-separated. **The switch:** empty means no mail is ever sent |
| `NOTIFY_FROM` | `fdb@example.org` | required once `NOTIFY_TO` is set |
| `SMTP_HOST` | your relay | required once `NOTIFY_TO` is set |
| `SMTP_PORT` | `587` | defaults to `587`; STARTTLS, so not `465` |
| `SMTP_USERNAME` | | required once `NOTIFY_TO` is set |
| `SMTP_PASSWORD` | | required once `NOTIFY_TO` is set. Mark it secret in Coolify |

A misconfigured relay cannot fail a run: `build_dist.py` notifies inside the failure
handler and prints `could not notify: ...` if the send itself raises.

Two more are set inside the compose file, not by hand:

- `POSTGRES_CONN_STR` — composed from the three Postgres variables above. Only set it
  yourself when moving the database out of the stack (step 5).
- `DLT_DATA_DIR` — `/srv/state/dlt`, inside the bind mount from step 2. It must
  persist across runs for load ids and pipeline state to line up.

Deploy. `history` becomes healthy first, then `pipeline` starts and idles on
`sleep infinity`, then `files`.

## 4. Attach the domain

Attach **`fdb.correlaid.org`** to the **`files`** service on port **80** — that exact
host, for the reason in step 0. Coolify writes the proxy labels and terminates TLS;
the `Caddyfile` binds `:80` and is host-agnostic, so it needs no per-environment
change.

Nothing else is exposed. `history` publishes no host port and is reachable only from
inside the stack.

## 5. Verify backups — do not skip this

**Needs your Coolify instance.** The history is the only artefact in this system that
cannot be rebuilt: upstream serves only today's export, so a lost database means the
change history restarts from zero.

Open the `history` service in Coolify and look for a **Backups** link. If it is there:
set a cron (daily is ample for a weekly pipeline) and an S3 destination.

If it is **not** there, move the database out rather than run unbacked. Coolify
supports backups for compose-defined databases, and restoring them since January
2026, but integrated backups for *git-based* compose deployments are still an open
enhancement ([coollabsio/coolify#7528](https://github.com/coollabsio/coolify/issues/7528))
— and this stack is git-based, because `pipeline` builds from the repo.

1. Coolify → **New Resource** → **PostgreSQL**, and enable its Backups tab.
2. Delete the `history` service from `docker-compose.yaml`.
3. Set `POSTGRES_CONN_STR` to the new resource's internal connection string.

That is the whole change. Nothing reaches the database except through that variable.

Sizing: the history is ~110 MB after one load — the raw text fields are large.
`pg_dump -Fc` compresses it well, but set retention with that in mind rather than
assuming a few MB. Growth after the first load tracks churn, not run count.

## 6. Run it once by hand

Before scheduling anything, run the real thing and read the output. In Coolify's
terminal for the `pipeline` container, or over SSH:

```sh
docker compose exec pipeline python scripts/build_dist.py --out /srv/public
```

Expect roughly:

```
ingest: 0 -> 2500 live programmes (load 1785398439.290075)
publish: 2500 programmes (2500 live), 49 columns, validated
/srv/public/data/programme.csv: 10.8 MB
/srv/public is ready to publish
```

This downloads 28 MB and takes about a minute. Then check what is actually served:

```sh
curl -sI https://fdb.correlaid.org/data/programme.csv | grep -i content-type
# text/csv; charset=utf-8

curl -sH 'Accept: text/turtle' -I https://fdb.correlaid.org/def/fdb | grep -i content-type
# text/turtle; charset=utf-8

curl -s -o /dev/null -w '%{http_code}\n' https://fdb.correlaid.org/state/
# 404
```

The middle one matters most: it is the check that the minted identifiers actually
dereference. `https://fdb.correlaid.org/id/dataset/foerderdatenbank-programme` should
answer the same way.

## 7. Add the scheduled task

**Needs your Coolify instance.** Coolify → this resource → **Scheduled Tasks**.

| Field | Value |
| --- | --- |
| Container | `pipeline` |
| Command | `python scripts/build_dist.py --out /srv/public` |
| Frequency | `0 3 * * 1` (Mondays, 03:00) |

A Coolify scheduled task rather than a cron inside the container, so a failed run is
visible in Coolify's logs instead of a container nobody looks at. The dataset's
`dct:accrualPeriodicity` declares weekly, so match that.

The run is safe to repeat: an unchanged export adds no versions, verified against the
real export. Two consecutive real runs produced 2500 versions and 2 load ids.

## 8. What fails a run, and what that protects

The pipeline is built to stop rather than publish something wrong. Every one of these
leaves last week's files serving:

| Guard | Fires when | Why it exists |
| --- | --- | --- |
| `contract.check_export` | a property was renamed or retyped upstream | a renamed property parses as null, and a null written into the history stays there — a load cannot be redone |
| dlt schema contract | an undeclared column or a changed type appears | the alternative is a column that exists for some rows and not others |
| duplicate `id_hash` | one key appears twice in a load | scd2 does not deduplicate; both rows would stay live forever |
| shrink guard | a run would retire >20% of live programmes | a truncated download looks exactly like mass deletion, and scd2 would close those windows for good |
| empty load package | extraction produced nothing | same |
| pandera schema | any value violates its column | new upstream category, bad pattern, broken uniqueness |

If a run fails, nothing has been written to `/data/fdb/public`. Read the error, fix
the cause, then republish **without** refetching:

```sh
docker compose exec pipeline python scripts/build_dist.py --no-ingest --out /srv/public
```

That works because the raw export fields are stored in the history, so a processing
fix can be re-applied to the exact input it was meant for.

## 9. Rollback

The published tree is plain files. Keep the previous CSV before a risky change:

```sh
cp /data/fdb/public/data/programme.csv /data/fdb/programme.csv.prev
```

The pipeline writes to a temporary name and renames, so a reader never sees a
half-written file, and a rollback is one `mv` back. Caddy stats per request, so no
reload is needed.

To restore the history itself, from a Coolify backup or a manual dump:

```sh
docker compose exec -T history pg_restore -U fdb -d fdb --clean < history.dump
```

Verified: `pg_dump -Fc` → `pg_restore` into a fresh database round-trips the history
intact.

## 10. Pre-flight checklist

Run these locally before deploying, and after any upstream surprise. Neither runs by
default — there is no CI in this repo, so they only run when you remember.

```sh
uv run pytest                 # the full suite: no network, no Docker
uv run pytest -m network      # the real 28 MB download and check_export
uv run pytest -m docker       # this whole stack, brought up and queried over HTTP
```

`-m docker` builds the image, starts all three services, runs the scheduled-task
command against the test fixture and asserts over real HTTP — including that the
history is not served. It is the closest thing to a deploy rehearsal.

If Postgres parity matters to you, `FDB_TEST_POSTGRES=<url> uv run pytest` also runs
the twelve history tests against Postgres instead of skipping them.

## Troubleshooting

**`POSTGRES_PASSWORD: generate a password in Coolify`** — the variable is unset. It
is deliberately required; an empty password would make trust authentication the
fallback.

**Permission denied writing `/srv/state`** — the host directories are missing or not
owned by uid 1000. Redo step 2.

**`ContractError` naming a `gsb:` property** — upstream changed the export's
structure. Investigate before overriding; if the change is understood and harmless,
regenerate the recorded structure with
`uv run python scripts/gen_contract.py <export_dir>` and read the diff.

**Caddy serves the CSV as `application/octet-stream`** — the `Caddyfile` was not
mounted, or its `header` matchers were reordered. Go's mime map does not know
`.ttl` or `.jsonld`; both are set explicitly. CSV is served as `text/csv`.

**A harvester fetches the metadata but downloads nothing** — the stack is answering
on a host other than `fdb.correlaid.org`, so the `dcat:downloadURL` in the dataset
document points somewhere that does not serve the file. Fix the attached domain
(step 4), not `uris.py` — see step 0.

**The portal shows the dataset with no title, licence or distribution** — something
split the description across documents. The harvester parses only the document it
fetched; `tests/test_dcat.py::test_the_dataset_document_is_self_contained` is what
keeps every node the dataset needs in the one file.
