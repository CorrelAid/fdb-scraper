# Results

Held-out split: **50 rows, 327 gold keywords**, of which **12 rows / 107 keywords**
contain at least one multi-token keyword. Metric is exact-span P/R/F1,
micro-averaged over keywords: a keyword counts only when *both* boundaries match.

The bar is not zero. A baseline that **never joins anything** scores **F1 0.878**,
because ~89.6% of gaps between adjacent tokens are correctly "split". On the rows
that do contain a compound it scores **0.658**.

## Encoder fine-tuning

Everything from here to *Not a labelling artefact* is the **first** round: 70 training
rows, no deterministic repairs. It is kept as measured, because the model comparison
and the ablations were run there and re-running them would not change what they show.
The current numbers are in *The second labelling round* below — held-out F1 0.968 on
131 training rows.

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

## The second labelling round

The first sample was random, which is what makes the held-out 50 publishable. It is
also why the training half was 93% single-token terms with a longest span of five: a
random draw over this column mostly asks the easy question. Two failures followed from
that, and they needed different instruments.

| gold term length | after 70 labels | after 131 |
| --- | --- | --- |
| 1 token | 711 | 1157 |
| 2 | 38 | 155 |
| 3 | 10 | **45** |
| 4 | 2 | 4 |
| 5 | 1 | 2 |
| 6–8 | **0** | **3** |

**Named entities — fixed by a rule.** The seven-token
`Beauftragte der Bundesregierung für Kultur und Medien BKM` came back in three pieces.
Its internal gaps scored 0.354 and 0.460: undecided, not wrong, which is what makes a
deterministic rule the right tool. Function-word glue repaired it without touching the
weights.

**Deverbal noun + NP — needed labels.** `Erhaltung polnische Sprache` was split at
**p=0.016** and `Pflege polnischer Sprache` at 0.098. Confident errors, and no
orthographic rule distinguishes them from `Waldgebiet Waldfläche Forst`, which is
correctly three keywords. Note what this rules out: uncertainty sampling cannot find
them either, because they are not uncertain. So
[`sample_uncertain.py`](sample_uncertain.py) draws on three criteria — gaps in
[0.2, 0.8], stratified by token count; a targeted pattern (a confident
adjective-noun join with a confident cut in front of it, which is that failure's
signature in the model's own numbers); and rows a human reported, by name.

61 rows drawn, all `train`. What it bought, measured on the untouched 50:

| held-out slice | rows | baseline | 70 labels + repairs | 131 labels |
| --- | --- | --- | --- | --- |
| all | 50 | 0.878 | 0.968 | 0.968 |
| has a multi-token keyword | 12 | 0.658 | 0.917 | **0.943** |
| has a 3+ token keyword | 5 | 0.550 | 0.818 | **0.906** |

Per-gap accuracy went 0.971 → 0.977. The pooled figure did not move, which is the
point of reporting slices: 45 of the 50 held-out rows were already right.

Three things worth stating plainly about this round:

- **The reported rows are training data.** `Erhaltung polnische Sprache` is segmented
  correctly now, and that is memorisation, not evidence — a labelled failure is worth
  more as a label than as a test case. The generalisation claim is the long-span slice,
  which contains none of them.
- **The repairs became redundant, and one was deleted.** They were worth +0.024 on the
  long-span slice before this round and 0.000 after it. Function-word glue fell from 105
  changed corpus values to 31 and is kept as a guard — a keyword ending in "der" is
  ill-formed German whatever a future checkpoint believes. The institution gazetteer
  (646 entries from `contact_info_institution`) went from three corrected rows to
  **zero**, measured both alone and on top of glue, and was deleted: a file that changes
  nothing is not a safety net, it is maintenance. `git log` has it.
- **A third rule never shipped.** A lexicon built from the 552 delimiter-using rows,
  filtered by the same morphology guard the distant-supervision set uses, contributed 16
  entries, changed six corpus rows and got **three of them wrong**
  (`Energiesystem Reibhausgasemissionen Energiespeicherung` is three keywords). A guard
  calibrated for a training label, where one bad example among hundreds is noise, is not
  good enough as an inference-time override.
- **Prose is excluded and therefore unmeasured.** A few dozen values are sentences
  rather than keyword lists. There is no defensible keyword partition of a relative
  clause, so those rows were kept out of the sample by function-word density, and
  whatever the model does to them is not covered by any number here.

Two rules were added to the labelling policy to make this round consistent, both in
[`build_labels.py`](build_labels.py): a nominalisation takes its complement with it
(`Stärkung der ländlichen Räume`), and a function word is never a boundary. The second
mirrors `postprocess` deliberately — a rule that fires at inference while the labels
disagree with it would make the measurement meaningless. Both directions are asserted
in the test suite, and neither rule contradicts any of the 181 labels.

## What is still unmeasured

- **Prose rows**, as above.
- **The second sample cannot be scored.** It was selected with the model's own
  probabilities, so it is training data by construction. A third round wanting a
  bigger *test* set has to draw it randomly.
- **One seed.** 0.968 is seed 0; `finetune_app.py::verify` runs 0,1,2 for variance.

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