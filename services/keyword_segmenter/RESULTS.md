# Results

Held-out split: **50 rows, 327 gold keywords**, of which **12 rows / 107 keywords**
contain at least one multi-token keyword. Metric is exact-span P/R/F1,
micro-averaged over keywords: a keyword counts only when *both* boundaries match.

The bar is not zero. A baseline that **never joins anything** scores **F1 0.878**,
because ~89.6% of gaps between adjacent tokens are correctly "split". On the rows
that do contain a compound it scores **0.658**.

## Encoder fine-tuning

A small German encoder, fine-tuned on 70 labelled rows as per-word BIO tagging.
Three models trained on the same data; the smallest ships because it scores best
and is the most permissively licensed.

| model | params | licence | per-gap | F1@0.5 | baseline |
| --- | --- | --- | --- | --- | --- |
| **deepset/gbert-base** | 110M | MIT | **0.974** | **0.967** | 0.878 |
| deepset/gbert-large | 340M | MIT | 0.962 | 0.949 | 0.878 |
| LSX-UniWue/ModernGBERT_1B | 1B | research-only | 0.969 | 0.959 | 0.878 |

At 70 training rows the extra capacity is unusable — the labels teach a task
mapping, not a language — so the smallest model wins outright. ModernGBERT was
kept in the comparison to answer "does encoder fine-tuning work at all"; it
cannot ship in a published open-data pipeline.

## Why this shape beats prompting

Three reasons, each measurable:

1. **Token classification is native to the architecture.** One label per word,
   one forward pass. The decoder approaches had to coerce a JSON partition out of
   a left-to-right model and add a fallback when the schema broke.
2. **Bidirectional context.** Adjectival agreement — the rule behind two thirds
   of the joins — depends on the noun *after* the adjective, which is the
   hardest direction for a left-to-right decoder.
3. **Class weighting addresses the 10.4% base rate directly.** Few-shot
   demonstrations set a prior and leave nothing to recalibrate; a trained
   classifier gives probabilities *and* a tunable threshold.

The prompted decoders behaved like 90–92% per-gap classifiers (F1 0.85–0.87);
clearing the 0.878 baseline needs ~93% per-gap, and beating it decisively ~95%.
`gbert-base` reaches 97.4%.

## Why prompting did not

Carried over from the previous round, for context:

| model | all F1 | P | R | hard F1 |
| --- | --- | --- | --- | --- |
| **baseline — never join** | **0.878** | 0.838 | 0.920 | 0.658 |
| Phi-4-mini-instruct (3.8B) | 0.872 | 0.843 | 0.902 | 0.667 |
| Qwen3.5-4B | 0.858 | 0.857 | 0.859 | 0.652 |
| Mistral-Nemo-Instruct-2407 (12B) | 0.719 | 0.777 | 0.670 | 0.727 † |

† Within ±0.076 noise — see prior round. **No model beat the baseline overall.**
The F1-optimal threshold on the calibrated decoders landed at tau → 1.0, i.e.
"never join", so the better model *became* the baseline at its operating point.
The honest summary is that the upstream field is mostly already a keyword list,
and the 5% that isn't cannot be separated out reliably enough with a decoder.

## Seed variance and ablation

A jump from 0.878 to 0.967 on 70 labelled rows deserves suspicion before it
deserves a write-up.

| run | per-gap | F1@0.5 | baseline |
| --- | --- | --- | --- |
| seed 0 + distant | 0.974 | 0.967 | 0.878 |
| seed 1 + distant | 0.971 | 0.965 | 0.878 |
| seed 2 + distant | 0.974 | 0.967 | 0.878 |
| **seed 0 gold-only** (70 train rows) | 0.973 | **0.965** | 0.878 |

Seed spread is ±0.002 — far smaller than the +0.089 effect. Distant supervision
moves the headline by 0.002; **gold-only matches it**, which is why distant
supervision is off by default. It is recorded in `distant.jsonl` for
reproducibility, not because it is load-bearing.

## Threshold sweep

| tau | gbert-base test F1 |
| --- | --- |
| 0.30 | 0.951 |
| 0.50 | **0.967** |
| 0.70 | 0.964 |
| 0.90 | 0.951 |
| baseline | 0.878 |

The curve peaks at 0.5 and falls on either side; there is no extra headroom
behind a tuned threshold, because class weighting already placed the operating
point there. (`test_selected_tau_OPTIMISTIC` picks the F1-best tau on test and is
not quoted as achieved performance — there is no validation split to fit on at
this data size.)

## Not a labelling artefact

All 12 hard held-out rows were re-read before drawing conclusions. The joins are
`Zirkuläre Wirtschaftssysteme`, `sozialer Wohnungsbau`, `psychische Gesundheit`,
`biogene Rohstoffe`, `Interreg VI`, `Industrie 4.0`,
`land- und forstwirtschaftlichen Wege`, `menschen mit behinderung` and similar —
almost all decided by plain adjectival agreement or an obvious proper name. One
of 107 (`bessere Grenzregion`) is genuinely arguable. The 0.658 baseline on the
hard subset is real failure of "split always", not disagreement about the answer.

## Architecture

The pipeline host is small, so inference runs as a service — but a much cheaper
one than the vLLM prompting service it replaced.

| | prompting (removed) | this |
| --- | --- | --- |
| hardware | L40S GPU | **2 CPU cores** |
| image | ~8GB (CUDA) | ~1GB |
| weights | 22GB checkpoint | 437MB |
| cold start | 76–222s | ~20s |
| full column | ~80 min sequential | **134s**, measured |

- **`tagger_app.py`** — Modal class, `min_containers=0` so cold start on first
  request and nothing is billed between rebuilds. `GPU` is a single constant:
  `None` by default because with the cache warm each export brings only a
  handful of new strings and cold start dominates, where the 1GB CPU image wins.
  Set `"T4"` for a one-off backfill (134s → ~40s).
- **Auth** — bearer token from a Modal Secret, compared with
  `hmac.compare_digest`. Sent as `X-Tagger-Token`, *not* `Authorization`:
  Modal's web-endpoint proxy reserves that header and the value never reaches
  the handler.
- **`segment/client.py`** — read-through cache in sqlite, keyed on
  `md5(keywords)`, plus deduplication. Repeated runs cost nothing, a rerun after
  a processing fix re-segments only what changed, and `publish()` can read a
  materialised result instead of re-running a model.

## Verdict

**Shippable as a service.** The encoder clears the baseline decisively (+0.089),
the service is cheap enough to leave deployed, and the cache makes reruns free.
The two properties that make the inferred column defensible in a dataset whose
selling point is a checked contract:

- Every keyword is a **contiguous span of the source**. Invention, omission
  and reordering are unrepresentable, not merely untested.
- Any label sequence maps to a valid segmentation, so there is no malformed
  output to handle — no retries, no fallbacks, no parse failures.

Before publishing, label ~500 rows. The current estimate rests on 50 held-out
rows containing 107 keywords; the effect (+0.089) is far larger than the seed
spread (±0.002), but the multi-token subset is thin.

One known loss worth fixing cheaply: `scraper._clean` collapses whitespace runs,
and in ~26 rows a double space *is* the author's separator. Feeding the
**uncollapsed** value to the tagger would hand it a free feature.

## Reproducing

```bash
uv run modal run services/keyword_segmenter/finetune_app.py           # all 3 models
uv run modal run services/keyword_segmenter/finetune_app.py::verify   # seeds + ablation
uv run modal run services/keyword_segmenter/finetune_app.py::train    # persist to Volume
```

Re-drawing or re-labelling the sample:

```bash
uv run python services/keyword_segmenter/sample_gold.py > services/keyword_segmenter/sample.jsonl
uv run python services/keyword_segmenter/build_labels.py              # validates every partition
```