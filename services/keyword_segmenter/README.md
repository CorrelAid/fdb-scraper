# keyword_segmenter

Splits the upstream `keywords` string into a list of keywords, using a fine-tuned
German encoder (`deepset/gbert-base`, 110M parameters, MIT) served from a Modal
CPU endpoint.

Held-out **F1 0.967** against **0.878** for doing nothing. Five prompted decoders
up to 12B all *lost* to doing nothing — see [RESULTS.md](RESULTS.md) for why, and
for the full measurement trail.

## The problem

`keywords` is one string per programme holding several keywords. 2440 of 2500
programmes have a value, and **2140 of those (87.7%) carry no separator signal at
all** — no semicolon, no comma, not even a double space. The keywords are joined by
single spaces, so a multi-word keyword is indistinguishable from several one-word
keywords without reading the German:

```
... Erhaltung polnische Sprache Pflege polnischer Sprache polnischsprachige Bürger
    Beauftragte der Bundesregierung für Kultur und Medien BKM
```

The last eight tokens are **one** keyword. Splitting on whitespace turns it into
eight useless ones. The delimiter-using minority is unreliable too: 226 of 552
comma-separated fields contain a space, and some commas sit *inside* a keyword.

The raw XML was checked directly — every one of the 2440 `gsb:keywords` properties
holds exactly one `<value>` element. There is no structure to recover; this export
puts its real structure in the classifier tree (`funding_area`, `funding_type`, …),
which the pipeline already parses.

## The approach

Per-word BIO tagging. Each whitespace token either **begins** a keyword or
**continues** the previous one:

```
tokens: Erneuerbare Energien Zuschuss Kommune
labels:      B          I        B        B
terms:  ["Erneuerbare Energien", "Zuschuss", "Kommune"]
```

Two properties make an inferred column defensible in a dataset whose selling point
is a checked contract:

- Every keyword is a **contiguous span of the source**. Invention, omission and
  reordering are unrepresentable, not merely untested.
- Any label sequence maps to a valid segmentation, so there is no malformed output
  to handle — no retries, no fallbacks, no parse failures.

What remains unverifiable is **boundary placement alone**, which is exactly what
the labelled sample measures.

## The bar, and why it is high

A baseline that **never joins anything** scores **F1 0.878**, because only
**10.4% of the gaps** between adjacent tokens are joins. That baseline is already
89.6% per-gap accurate, and a spurious join costs about **2.5×** what a caught
compound earns: it destroys two gold keywords and invents a wrong one, while a
correct join wins one.

Metric is exact-span P/R/F1, micro-averaged over keywords — a keyword counts only
when *both* boundaries are right. Reported alongside is the score restricted to
rows containing a multi-token keyword (baseline 0.658), because without that cut the
easy majority hides everything the model is for.

`gbert-base` reaches **97.4% per-gap**. Three reasons an encoder can where a
prompted decoder could not:

1. It **is** token classification — one label per word, native to the architecture.
2. A bidirectional encoder sees both sides of every gap. Adjectival agreement, the
   rule behind two thirds of the joins, depends on the noun *after* the adjective —
   the hardest direction for a left-to-right decoder.
3. Class weighting addresses the 10.4% base rate directly. Few-shot demonstrations
   cannot: they set a prior and leave nothing to recalibrate.

The model already knows German from pretraining; 70 labelled rows only teach the
task mapping. That is why so little data suffices — and the ablation confirms it:
gold-only scores 0.965 against 0.967 with distant supervision added.

## Architecture

The pipeline host is small, so inference runs as a service — but a much cheaper one
than the vLLM prompting service it replaced.

| | prompting (removed) | this |
| --- | --- | --- |
| hardware | L40S GPU | **2 CPU cores** |
| image | ~8GB (CUDA) | ~1GB |
| weights | 22GB checkpoint | 437MB |
| cold start | 76–222s | ~20s |
| full column | ~80 min sequential | **134s**, measured |

- **`tagger_app.py`** — Modal class, `min_containers=0` so cold start on first
  request and nothing is billed between rebuilds. `GPU` is a single constant: `None`
  by default because with the cache warm each export brings only a handful of new
  strings and cold start dominates, where the 1GB CPU image wins. Set `"T4"` for a
  one-off backfill (134s → ~40s).
- **Auth** — bearer token from a Modal Secret, compared with `hmac.compare_digest`.
  Sent as `X-Tagger-Token`, *not* `Authorization`: Modal's web-endpoint proxy
  reserves that header and the value never reaches the handler.
- **`segment/client.py`** — read-through cache in sqlite, keyed on
  `md5(keywords)`, plus deduplication. Repeated runs cost nothing, a rerun after a
  processing fix re-segments only what changed, and `publish()` can read a
  materialised result instead of re-running a model.

## Usage

Train once, and persist the weights to a Volume:

```bash
uv sync --extra segmenter
uv run modal run services/keyword_segmenter/finetune_app.py::train
```

Deploy the endpoint:

```bash
modal secret create fdb-tagger-token TAGGER_TOKEN=$(openssl rand -hex 32)
uv run modal deploy services/keyword_segmenter/tagger_app.py
```

Call it from the pipeline:

```python
from segment.client import TaggerClient

with TaggerClient() as client:            # FDB_TAGGER_URL, FDB_TAGGER_TOKEN
    results = client.segment(df["keywords"].drop_nulls().to_list())
# [{'terms': ['Erneuerbare Energien', 'Zuschuss'], 'group_sizes': [2, 1]}, ...]
```

Or by hand:

```bash
curl -X POST "$FDB_TAGGER_URL" -H 'content-type: application/json' \
  -H "x-tagger-token: $FDB_TAGGER_TOKEN" \
  -d '{"keywords": ["Erneuerbare Energien Zuschuss Kommune"]}'
```

Running the model in process is also supported (`segment.tagger.KeywordTagger`)
and is what the `model`-marked tests use — but it needs `--extra tagger` (torch)
and the weights fetched locally, which the pipeline host cannot afford:

```bash
modal volume get fdb-keyword-tagger keyword_tagger models/
```

Evaluation and the checks behind the numbers:

```bash
uv run modal run services/keyword_segmenter/finetune_app.py           # all 3 models
uv run modal run services/keyword_segmenter/finetune_app.py::verify   # seeds + ablation
```

Re-drawing or re-labelling the sample:

```bash
uv run python services/keyword_segmenter/sample_gold.py > services/keyword_segmenter/sample.jsonl
uv run python services/keyword_segmenter/build_labels.py   # validates every partition
```

## Layout

| Path | Role |
| --- | --- |
| `segment/tokens.py` | Tokenisation, the partition contract, join↔size conversion |
| `segment/dataset.py` | BIO labels; distant supervision from delimited rows |
| `segment/metric.py` | Exact-span P/R/F1, micro-averaged, plus the threshold sweep |
| `segment/tagger.py` | Inference: loads the fine-tuned model, segments a batch |
| `segment/client.py` | **Pipeline entry point.** Endpoint client + sqlite cache |
| `tagger_app.py` | The deployed service: CPU container, token auth, POST endpoint |
| `finetune_app.py` | Modal: train, score, save to Volume, seed/ablation checks |
| `sample_gold.py` | Draws the fixed 120-row sample (seeded, reproducible) |
| `build_labels.py` | Hand-written gold partitions and the labelling rules used |
| `labels.jsonl` | 120 labelled rows: 70 `train`, 50 `test` |
| `distant.jsonl` | 61 rows recovered from delimited fields (not load-bearing) |

## Tests

```bash
uv run pytest tests/test_keyword_segmenter.py        # 34, no weights or network
uv run pytest tests/test_keyword_segmenter.py -m model    # 4, needs local weights
uv run pytest tests/test_keyword_segmenter.py -m network   # 4, needs the endpoint
```

The default set covers the partition contract, the metric's behaviour on
wrongly-split and wrongly-joined predictions, the threshold sweep, the cache
(against a stub endpoint, so read-through and dedup are checked on every run), and
a **rule-based audit of the gold labels**: German attributive adjectives must be
inflected, so `Erneuerbare Energien` is one keyword and `digital Websites` is two.
That audit exists because the labelling default (all-ones) is also the baseline
being beaten, so a missed compound would silently flatter the baseline.

The `network` set includes auth: a missing token, a wrong token, and a wrong token
*of the correct length*, so the check cannot pass on length alone.

## Data provenance

- **Gold labels**: 120 rows drawn by seeded sample, hand-labelled. 34 of 51 joins
  follow from the inflection rule; 17 rest on judgement (proper names, English
  terms, numbers). One annotator, no second opinion — the honest limitation.
- **Distant supervision**: delimiter-using rows with fields of 4+ tokens
  **discarded**. Taking every comma-field as one keyword gives a 65.5% join rate
  against a true 10.4%, which would train exactly the over-joining bias to avoid.
  Off by default, since gold-only matches it.
- **No leakage**: held-out values are excluded from distant supervision, and **no
  multi-word keyword appears in both splits** — all 26 test compounds are unseen.

## How it is wired into the pipeline

Not in `process.py`, which is transforms-only, and not in `collect()`, which reads
one export with no database:

- **`keywords_extracted`**, last in `PUBLISHED_FIELDS`. Raw `keywords` is published
  unchanged beside it. Absent from `collect()`, like the history columns: nothing in
  a single export can supply it.
- **`schema.ORIGIN`** marks every published column `upstream`, `derived` or
  `inferred`, and is surfaced by `describe()` and as `fdb:origin` on every column of
  the DCAT table schema, with the minted term defined in `dcat/def/fdb.ttl`. A CSV
  cannot show that one of its columns is a model's reading of another.
- **`history.segment_keywords`** calls `TaggerClient` after a load and merges the
  results into a `keyword_segments` table, keyed on `md5(keywords)`. Merged, not
  scd2'd: a segmentation is a function of the string, so it has no history. Skipped
  when `FDB_TAGGER_URL`/`FDB_TAGGER_TOKEN` are unset — recording the export must not
  depend on a model service being up.
- **`publish()`** reads that table and `process()` does a dict lookup, as it already
  does for `documents`. Inference is not bit-reproducible, so publishing twice from
  one history has to read a materialised result rather than recompute it. An
  unsegmented value publishes as null, never as a guess.
- **The span invariant** is a frame-level pandera check over `keywords` and
  `keywords_extracted`: every keyword is a contiguous span of the raw string and the
  spans cover it exactly, so invention, omission, reordering and editing fail the
  publish. Boundary placement is what stays unverifiable, and what the held-out score
  measures.

Still open: label ~500 rows. The current estimate rests on 50 held-out rows
containing 107 keywords; the effect (+0.089) is far larger than the seed spread
(±0.007), but the multi-token subset is thin — which is why the published metadata
states the score and the sample rather than claiming the column is correct.

One known loss worth fixing cheaply: `scraper._clean` collapses whitespace runs,
and in ~26 rows a double space *is* the author's separator. Feeding the
**uncollapsed** value to the tagger would hand it a free feature.
