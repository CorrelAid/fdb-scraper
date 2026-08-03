# keyword_segmenter

Splits the upstream `keywords` string into a list of keywords with a fine-tuned
German encoder (`deepset/gbert-base`, 110M parameters, MIT), served from a Modal CPU
endpoint.

Held-out **F1 0.968** against **0.878** for doing nothing, and **0.906** on the rows
whose keywords run three tokens or longer. Five prompted decoders up to 12B all *lost*
to doing nothing — [RESULTS.md](RESULTS.md) has the full measurement trail.

## The problem

`keywords` is one string per programme holding several keywords. 2440 of 2500
programmes have a value, and **2140 of those (87.7%) carry no separator at all** — no
semicolon, no comma, not even a double space. A multi-word keyword is therefore
indistinguishable from several one-word keywords without reading the German:

```
... Erhaltung polnische Sprache Pflege polnischer Sprache polnischsprachige Bürger
    Beauftragte der Bundesregierung für Kultur und Medien BKM
```

The last eight tokens are **one** keyword; so are `Erhaltung polnische Sprache` and
`Pflege polnischer Sprache`. Splitting on whitespace turns twelve keywords into
twenty-six useless ones. The delimiter-using minority is unreliable too: 226 of 552
comma-separated fields contain a space, and some commas sit *inside* a keyword.

The raw XML was checked directly — every one of the 2440 `gsb:keywords` properties
holds exactly one `<value>`. There is no structure to recover; this export puts its real
structure in the classifier tree (`funding_area`, `funding_type`, …), which the pipeline
already parses.

## The approach

**Predict the gaps, not the strings.** Each whitespace token either begins a keyword or
continues the previous one — per-word BIO tagging, which is what an encoder is natively
fine-tuned for:

```
tokens: Erneuerbare Energien Zuschuss Kommune
labels:      B          I        B        B
terms:  ["Erneuerbare Energien", "Zuschuss", "Kommune"]
```

Two invariants follow, and they are why an *inferred* column is defensible in a dataset
whose selling point is a checked contract:

- Every keyword is a **contiguous span of the source**. Invention, omission and
  reordering are unrepresentable, not merely untested.
- **Any** label sequence maps to a valid segmentation. There is no malformed output to
  handle — no retries, no fallbacks, no parse failures. Asking a model for group sizes
  instead made a quarter of replies unusable: well-formed integer lists that did not sum
  to the token count.

What remains unverifiable is **boundary placement alone**, which is exactly what the
labelled sample measures.

Why an encoder beat five prompted decoders, briefly: token classification is native to
the architecture; a bidirectional model sees both sides of every gap, and adjectival
agreement — the rule behind two thirds of the joins — depends on the noun *after* the
adjective; and class weighting addresses the 10.4% join rate directly, where few-shot
demonstrations only set a prior.

### One deterministic repair

A German keyword neither begins nor ends with an article, preposition or conjunction, so
`Beauftragte der` and `Bundesregierung für` are ill-formed on their face. Forcing a
function word to bind on both sides ([`postprocess.py`](segment/postprocess.py)) fixes
that without touching the weights, and cannot invent text — it only ever *adds* joins to
an already valid partition, and never crosses a delimiter the author actually wrote.

It was worth +0.024 on the long-span slice against the first checkpoint and **nothing
measurable** against the current one: the second labelling round taught the model the
rule, and the repair's footprint fell from 105 changed values to 31. It stays as a
guard, since those boundaries are ill-formed whatever a future retrain believes. A second
rule — a 646-entry gazetteer of institution names — reached zero changed rows and was
deleted rather than kept as decoration.

## The bar, and the numbers

A baseline that **never joins anything** scores **F1 0.878**, because only **10.4% of
gaps** are joins. That baseline is already 89.6% per-gap accurate, and a spurious join
costs about **2.5×** what a caught compound earns: it destroys two gold keywords and
invents a wrong one, while a correct join wins one. So the operating point leans towards
splitting, and the metric is exact-span P/R/F1 micro-averaged over keywords — a keyword
counts only when *both* boundaries are right.

| held-out slice | rows | never join | 70 labels | 131 labels |
| --- | --- | --- | --- | --- |
| all | 50 | 0.878 | 0.968 | **0.968** |
| contains a multi-token keyword | 12 | 0.658 | 0.917 | **0.943** |
| contains a 3+ token keyword | 5 | 0.550 | 0.818 | **0.906** |

Per-gap accuracy is 0.977. The slices matter more than the headline: 45 of the 50
held-out rows were already right before the second round, so a pooled number cannot move
much and cannot show what changed. The five-row slice is thin, which is the honest caveat
on it.

## The labels

131 training rows, 50 held out, from two samples. [`labels.jsonl`](labels.jsonl) is built
from both by [`build_labels.py`](build_labels.py), which documents the seven labelling
rules and the bias they resolve toward (splitting: an over-split term is a recoverable
search miss, a wrongly-joined one is a term no consumer will ever match).

- **[`sample_gold.py`](sample_gold.py)** — 120 rows, seeded random draw. The held-out 50
  comes from here and **never grows**: it is the only reason numbers stay comparable
  across rounds.
- **[`sample_uncertain.py`](sample_uncertain.py)** — 61 rows, all `train`. A random draw
  over this column mostly asks the easy question, which is why the first round's terms
  were 93% single-token with a longest span of five, and why seven-token agency names
  came back split. This draw targets that: gaps at p ∈ [0.2, 0.8] stratified by token
  count, plus a pattern aimed at *confident* errors (`Erhaltung polnische Sprache` was
  wrong at p=0.016, so uncertainty sampling cannot see it), plus rows a human reported by
  name. It is selected with the model's own probabilities, so it can only ever be
  training data.

Sentences are excluded, deliberately. A few dozen values are prose rather than keyword
lists, there is no defensible keyword partition of a relative clause, and labelling taste
as gold would train the one failure that already dominates — over-joining.

## Architecture

The pipeline host is small, so inference is a service — a far cheaper one than the vLLM
prompting service it replaced.

| | prompting (removed) | this |
| --- | --- | --- |
| hardware | L40S GPU | **2 CPU cores** |
| image | ~8GB (CUDA) | ~1GB |
| weights | 22GB checkpoint | 437MB |
| cold start | 76–222s | ~20s |
| full column | ~80 min sequential | **158s**, measured |

- **[`tagger_app.py`](tagger_app.py)** — Modal class, `min_containers=0`, so cold start
  on first request and nothing billed between runs. `GPU` is one constant, `None` by
  default: with the cache warm each export brings a handful of new strings, cold start
  dominates, and the 1GB CPU image wins. Set `"T4"` for a backfill.
- **Auth** — bearer token from a Modal Secret, compared with `hmac.compare_digest`. Sent
  as `X-Tagger-Token`, *not* `Authorization`: Modal's web-endpoint proxy reserves that
  header and the value never reaches the handler.
- **[`client.py`](segment/client.py)** — read-through sqlite cache plus deduplication,
  keyed on `md5(revision + keywords)`. The pipeline stores results in `keyword_segments`
  and publishes from there, because inference is not bit-reproducible across container
  generations and two publishes of one history have to agree.
- **[`revision.py`](segment/revision.py)** — `<code>.<weights>`, reported with every
  response. It exists because of a real failure: keyed on the string alone, adding the
  function-word repair left 2340 stale rows that a rerun had no reason to touch, and the
  improvement reached nothing until someone cleared the cache by hand. A code change or a
  retrain now invalidates by construction.

## Usage

```bash
uv sync --extra segmenter

# train, evaluate, persist to the Volume
uv run modal run services/keyword_segmenter/finetune_app.py::train
uv run modal run services/keyword_segmenter/finetune_app.py            # all 3 models
uv run modal run services/keyword_segmenter/finetune_app.py::verify    # seeds + ablation

# deploy
modal secret create fdb-tagger-token TAGGER_TOKEN=$(openssl rand -hex 32)
uv run modal deploy services/keyword_segmenter/tagger_app.py
```

From the pipeline (`FDB_TAGGER_URL`, `FDB_TAGGER_TOKEN`):

```python
from segment.client import TaggerClient

with TaggerClient() as client:
    results = client.segment(df["keywords"].drop_nulls().to_list())
# [{'terms': ['Erneuerbare Energien', 'Zuschuss'], 'group_sizes': [2, 1]}, ...]
```

By hand:

```bash
curl -X POST "$FDB_TAGGER_URL" -H 'content-type: application/json' \
  -H "x-tagger-token: $FDB_TAGGER_TOKEN" \
  -d '{"keywords": ["Erneuerbare Energien Zuschuss Kommune"]}'
```

In process, which is what the `model`-marked tests use — needs `--extra tagger` and the
weights fetched locally:

```bash
modal volume get fdb-keyword-tagger keyword_tagger models/
```

Re-drawing the samples. The random draw is the held-out set's source and should not be
re-drawn without a reason; the uncertainty draw depends on the current checkpoint, so
re-running it after a retrain returns different rows, which need labelling before
`build_labels.py` will accept them:

```bash
uv run python services/keyword_segmenter/sample_gold.py > .../sample.jsonl
uv run --extra tagger python services/keyword_segmenter/sample_uncertain.py > .../sample_uncertain.jsonl
uv run python services/keyword_segmenter/build_labels.py
```

## Files

| | |
| --- | --- |
| `segment/tokens.py` | Tokenisation and the partition contract |
| `segment/tagger.py` | The model, in process |
| `segment/postprocess.py` | The function-word repair |
| `segment/metric.py` | Exact-span P/R/F1 and the threshold sweep |
| `segment/dataset.py` | BIO labels from gold and from delimited rows |
| `segment/client.py` | HTTP client, revision-keyed cache |
| `segment/revision.py` | What produced a segmentation |
| `tagger_app.py` | The Modal endpoint |
| `finetune_app.py` | Training, evaluation, ablations |
| `sample_gold.py`, `sample_uncertain.py` | The two draws |
| `build_labels.py` | The labels and the rules behind them |
| `labels.jsonl` | 181 labelled rows, built from both samples |
| `distant.jsonl` | 61 rows recovered from delimited fields (measured not load-bearing) |

## Known limits

- **Prose rows are unmeasured.** Excluded from labelling, so no number here covers them.
- **The second sample cannot be scored.** It was selected with the model's own
  probabilities. A round wanting a bigger *test* set has to draw it randomly.
- **One seed.** 0.968 is seed 0; `::verify` runs 0, 1, 2 (spread ±0.002).
