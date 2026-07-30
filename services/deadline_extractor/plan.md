# deadline_extractor — plan

Nothing is built yet. This records what the export actually contains, what the
target vocabulary should be, and which numbers are measured versus estimated, so
the first commit does not have to rediscover it.

Every count below comes from the 2500-programme export in `data/`, not from
memory. Numbers marked *estimated* are a read of a few dozen samples and are the
main thing the labelled set has to settle.

## The problem

"Until when can I apply" is not a field. Upstream has a slot for it and left it
empty, and the answer is scattered across free German prose in several columns.

The slot: `Foerdertermin` is a `gsb:LinkClassifier` in the export, wired to 2497
of 2500 programmes, and `<links/>` is empty on every one of them. That is why
`foerdertermin` sits in `schema.DROPPED_FIELDS`. Worth a notebook watch like E9
does for `should_not_be_indexed` — if upstream ever populates it, it beats any
model here and this service becomes a backfill.

## Where the information is

Swept all 28 string columns for date expressions. Only four carry anything, and
two of the four carry no deadlines at all:

| published column | export field | rows with a date | verdict |
| --- | --- | --- | --- |
| `deadlines` | `procInfluence` | 224 | the curated deadline section; primary source |
| `description` | `summary` | 92 | real deadlines mixed with heavy noise |
| `procedure` | `procMethod` | 17 | small, clean |
| `legal_basis` | `bodyText` | 2313 | both application deadlines and Richtlinie validity |
| `legal_citation` | `procDescription` | 1623 | **no deadlines** — all Richtlinie issuance dates |
| `legal_requirements` | `regulatoryFWork` | 75 | **no deadlines** — eligibility cut-offs |

`title`, `header`, `teaser`, `keywords`, `remark`, `progress` and
`competence_descr` are zero or single digits.

The last two rows are the useful surprise. `legal_citation` is 4056 dates of the
form `Richtlinie des BMBF vom 3. Mai 2024`, and `legal_requirements` says things
like `zum Stichtag 31.12.2023 ordentliche Mitglieder` and `Die Maßnahme beginnt
frühestens am 01.10.2026`. Neither is a source, but both are **hard negatives**
that use the exact cues a real deadline uses — several thousand labelled
negatives for free, without hand-marking.

## How much there is to extract

Rows with a date sitting near an application cue, first field wins:

| | rows |
| --- | --- |
| tier 1 (`deadlines`, `procedure`, `description`) | 222 |
| `legal_basis` only | 878 |
| no cue-date anywhere | 1400 |

Of the ~1100 candidate rows, *estimated* 500–700 hold a real application
deadline — 20–28% of programmes. Almost all of that uncertainty is in the
878-row `legal_basis` bucket, where the hit rate is *estimated* 40–60% because
Richtlinie validity dates outnumber application deadlines. That bucket is where
the labelling effort belongs.

Not dates, but still answers to "when can I apply":

- ~38 rows `innerhalb von 3 Monaten` (relative duration)
- ~165 rows `laufend` / `fortlaufend` / `Fristen werden auf der Website bekanntgegeben`
- 194 rows editorially closed via `status_note` (`Antragstellung nicht mehr möglich`), plus 25 past-tense in `deadlines`

Counting those, useful deadline information for *estimated* 750–950 programmes.

The 1400 no-cue-date rows are "no date found", **not** "no deadline exists". All
206 `buergschaft` / `beteiligung` / `garantie` programmes land here, which fits —
guarantees and equity are rolling instruments — but that is inference, not
something the text states.

## The contract

The same move that makes `keyword_segmenter` defensible: constrain the model so
the failure modes that matter are unrepresentable rather than untested.

1. A regex enumerates every date expression with character offsets. No model.
2. The model is shown the source text plus the numbered candidates and returns a
   **candidate index plus a role**, never a date string.
3. Validation on 100% of rows: index in range, no index twice, role and model in
   closed vocabularies, emitted date equals the text at its recorded span.

So a fabricated, dropped or reordered deadline is unrepresentable. What stays
unverifiable is role assignment and tense — which is exactly what the labelled
sample measures.

Invalid reply → one retry with the error fed back → fall back to the regex
baseline, and record which rows the model actually decided.

## Target vocabulary: XFLB, not an invented enum

XFLB 2.0.0 (`urn:xoev-de:kosit:standard:xflb_2.0.0`, KoSIT/FITKO) already models
this, and its `Antragsinformationen` block maps almost 1:1 onto the FDB fields —
not a coincidence, XFLB exists to harmonise these portals:

| XFLB | export field | published as |
| --- | --- | --- |
| `antragsfrist` | `procInfluence` | `deadlines` |
| `verfahrensablauf` | `procMethod` | `procedure` |
| `bearbeitungsdauer` | `progress` | `processing_time` |
| `erforderlicheUnterlagen` | `competenceDescr` | `required_documents` |

`FristZeitlicheEingrenzung` is a choice of **exactly one**:

| branch | type | what it covers here |
| --- | --- | --- |
| `fristdatum` | `xs:date` | fixed date — 132 single-date rows |
| `fristJaehrlich` | `MonatTag` | annual recurrence — `jeweils der 15. April` |
| `fristFest` | `Dauer` | `innerhalb von 3 Monaten` |
| `fristSpanne` | min/max `Dauer` | duration range |
| `sonstige` | literal `'ja'` | flag, no value — some other arrangement |

Two consequences worth stating plainly.

`antragsfrist` is `0..1` and the eingrenzung is choice-of-one, so **XFLB cannot
express two-stage or multiple cut-offs**. Collapsing a two-stage procedure to its
first (sketch) deadline is not our simplification, it is what the target schema
requires.

And two things XFLB drops that this dataset needs:

- `fristJaehrlich` holds one `MonatTag`, but 35 rows have two
  (`jeweils der 15. April und der 15. Oktober`). Earliest into `fristJaehrlich`,
  verbatim source into `bemerkung` (`Beschreibung`, maxLength 8000).
- **`closed` is not representable.** XFLB has no expired flag, so
  `Projektskizzen konnten bis zum 30.06.2024 eingereicht werden` becomes a
  `fristdatum` in the past, indistinguishable from an open deadline that lapsed.
  Keep our own flag alongside.

Pull the XSD from https://gitlab.opencode.de/OC000029002073/xflb and generate the
validator rather than hand-writing it, and write the gold labels directly as
XFLB `Antragsfrist` fragments so the label set is checkable against the official
schema.

### Spec contradiction, for whoever implements it

The `fristJaehrlich` prose says format `--MM-DD`. The `MonatTag` type it
references is restricted to German `TT.MM`:

```
((0[1-9]|[12][0-9]|3[01])\.(01|03|05|07|08|10|12))|((0[1-9]|[12][0-9]|30)\.(04|06|09|11))|((0[1-9]|1[0-9]|2[0-9])\.02)
```

Same document, two formats. Implement to the pattern, not the prose. `RENAMES`
in `schema.py` already documents this class of upstream contradiction.

## `deadline_model` — do this first

Higher coverage than the date and mostly rule-decidable, because the *procedure*
is stated in words even where no date is given. Measured cue counts:

| cue | tier 1 w/ date | tier 1 no date | `legal_basis` |
| --- | --- | --- | --- |
| `zweistufig` / `2-stufig` | 82 | 124 | 93 |
| `erste`/`zweite Verfahrensstufe` | 58 | 68 | 52 |
| `Skizze` | 89 | 79 | 213 |
| `Einreichungsrunde` / `Förderrunde` / `Call` | 15 | 6 | 46 |
| `jeweils der/zum` | 13 | 7 | 152 |
| `Stichtage` (plural) | 17 | 48 | 79 |
| `laufend` / `fortlaufend` / `jederzeit` | 23 | 142 | 832 |
| past tense / `nicht mehr möglich` | 25 | 10 | 388 |

206 tier-1 rows say `zweistufig` and carry **no date at all**, so this column is
populatable where `application_deadline` has to stay null.

Vocabulary from the EU Funding & Tenders portal's `deadlineModel`
(`single-stage`, `two-stage`, `multiple cut-off`), extended with two values it
lacks and this data needs — `rolling` and `closed` are the two largest buckets.

Rules, first match wins:

1. `closed` — `status_note` editorial closure, or past tense in the deadline text
2. `multiple cut-off` — `Einreichungsrunde` / `Förderrunde` / plural `Stichtage` / `jeweils der` / ≥2 recurring month-days
3. `two-stage` — `zweistufig` or `Verfahrensstufe` (strong, unambiguous)
4. `rolling` — `laufend` / `fortlaufend` / `jederzeit`
5. `single-stage` — one deadline, none of the above
6. `unknown`

The model is called only on the ambiguous residue. The one weak cue is `Skizze`
alone: mentioning a Projektskizze does not prove a two-stage procedure, so
`Skizze` without `zweistufig`/`Verfahrensstufe` goes to the model.

**two-stage and multiple cut-off co-occur.** Real row:

> `Das Antragsverfahren ist zweistufig. ... Bewertungsstichtage sind jeweils der
> 15. April und 15. Oktober.`

EU's enum forces one value. Publish the single EU-compatible value with a stated
precedence (cut-off wins — it is what a consumer plans around) plus
`has_sketch_stage` and `is_recurring` booleans, so nothing is silently dropped.

### Why the model gates the date

`deadline_model` decides what a date *means*, which turns the collapse rules into
mechanics instead of judgement:

- `two-stage` → publish the sketch deadline (the first one)
- `multiple cut-off` → publish the next cut-off, keep the rest in `all_deadlines`
- `rolling` / `unknown` → `application_deadline` **must** be null
- `closed` → the date is historical, flagged as such

That last constraint is a hard invariant of the same character as
`sum(sizes) == n_tokens`: *model says rolling ⇒ no date published*. It removes the
false-positive class structurally rather than trusting per-candidate judgement.

## Candidate extraction: what the regex must and must not do

Audited by masking out what a strict pattern catches and inspecting the residue.
**Strictness is load-bearing** — requiring a 4-digit year is the only thing
keeping ~5000 Richtlinie section numbers out:

| residue pattern | count | what it actually is |
| --- | --- | --- |
| `dd.mm.` no year | 4453 | `3.1.`, `6.9.`, `Nummer 2.1.` — section numbering |
| `dd.mm.yy` | 595 | `5.1.10`, `2.1.11` — same |
| `Monat YYYY` | 323 | `Stand September 2023`, DIN editions — never deadlines |
| ISO `yyyy-mm-dd` | 13 | Aktenzeichen, phone numbers, file paths |

Three defects to fix before anything is built:

1. **Greedy year attachment.** A pattern matching `23. September` and stopping
   drops the year, turning `Die Bewerbungsfrist endet am 23. September 2024` into
   an annual recurrence instead of a fixed date — a *wrong answer*, not a miss,
   and precisely the XFLB choice the whole thing hangs on. 1 occurrence in
   `deadlines`, 6 in `description`, **14466 in `legal_basis`**. Trivial in tier 1,
   catastrophic in `legal_basis`.
2. **`frist` matches inside `befristet`.** `ist befristet bis zum 31.12.2029` is
   Richtlinie validity, not a deadline. Excluding the `befristet` family drops
   the `legal_basis`-only bucket from 920 to 878. Exclude that family only — do
   not require the cue at word start, or `Antragsfrist` and `Bewerbungsfrist`
   stop matching.
3. **Calendar validation.** Upstream ships `Richtlinie ... vom 00. November 2020`.
   Day `00`. XFLB's own `MonatTag` pattern rejects `31.02`, which suggests its
   authors hit the same thing.

Note also that Polars' Rust regex has no lookbehind, so cue exclusion has to run
in Python, not in a `pl.col(...).str.contains(...)`.

## The `legal_basis` windower is the main open risk

`legal_basis` is 80% of the coverage and averages 17.5k characters — the full
text does not fit an 8k context, let alone with few-shot demos. So a regex has to
cut cue-windows first, which reduces 24602 candidates to ~1967 over ~1041 rows.

That moves the risk somewhere new. In `keyword_segmenter` the deterministic half
was whitespace tokenisation, trivially correct, so the arithmetic invariant
covered everything that could go wrong. A cue windower is not trivially correct:
**it can silently drop a real deadline and no invariant catches a window that was
never cut.**

So this is the first thing to measure, not the last. Hand-mark every application
deadline in ~40 `legal_basis` rows and check what fraction the windows contain.
Below ~0.95 the problem is the windowing, not the model, and no amount of model
quality fixes it.

## The bar

Baseline: last full date preceded by a `bis (zum|spätestens)` cue, treated as the
application deadline. It will score well overall — 132 rows have exactly one date
— so report it, then report the **hard cut** separately: the 120 tier-1 rows with
≥2 distinct dates, plus the past-tense rows. Without that cut the easy majority
hides everything the model is for, the same way it does in `keyword_segmenter`.

Metric: exact match on `(normalised date, role)` pairs, micro-averaged, **plus
precision reported on its own**. This is the one place the segmenter's instinct
inverts. There, "when unsure, split" is right because an over-split keyword still
matches a search. Here a wrong date is worse than a missing one, because a
consumer plans against it. So the fallback on an invalid reply is `unknown`, not
a guess.

Roles: `application_deadline`, `sketch_deadline`, `project_completion`,
`payment_claim`, `report_due`, `programme_validity`, `other`. Only the first two
reach the output. The rest exist purely as **distractors** — somewhere for the
model to file the Abrechnung and Mittelabruf dates instead of misfiling them as
deadlines. They are precision armour, not published columns. Real row showing why:

> `Abschluss der Maßnahmen: Investitionen müssen bis zum 31.12.2028 abgeschlossen
> sein. Mittelabruf: ... bis zum 30.03.2029 ... Abrechnung: ... bis zum 30.06.2030`

Three `bis zum` dates, none of them an application deadline. A last-date
heuristic returns the Abrechnung date.

## Labels

~120 rows, seeded and reproducible like `keyword_segmenter/sample_gold.py`.
Stratified across the buckets that actually differ, not uniformly — uniform
sampling gives ~15 rows from `deadlines` and nothing to measure:

- dated `deadlines` rows
- undated `deadlines` rows (rolling, relative, recurring)
- past-tense / closed rows
- `legal_basis`-only rows (the largest and least certain bucket)
- **negatives**: rows with a plausible-looking date and no application deadline,
  drawn from `description`, `legal_requirements` and `legal_citation`

Written as XFLB `Antragsfrist` fragments, validated against the official XSD.

## Proposed columns

| column | source |
| --- | --- |
| `deadline_model` | rules on all 2500 rows, model on the residue |
| `has_sketch_stage`, `is_recurring` | rules; preserve what EU's enum forces out |
| `application_deadline` | XFLB `fristdatum` / `fristJaehrlich` / `fristFest` / `fristSpanne` / `sonstige` |
| `all_deadlines` | EU-style; keeps multi-cut-off rows lossless |
| `deadline_source_field`, `deadline_char_span` | provenance; enforces the span invariant |
| `deadline_decided_by` (`rule` \| `model`), `fell_back` | auditability |
| `programme_valid_until` | Richtlinie Geltungsdauer from `legal_basis`; the honest replacement for the dropped `date_of_expiration` |

`deadline_decided_by` matters for the same reason `fell_back` does in
`keyword_segmenter`: an inferred column that cannot say which rows a model
touched is not auditable. It also lets consumers use the rule-decided rows
without inheriting the model's error bar.

Do **not** publish an `application_open` boolean. It is a function of the publish
date, which breaks the determinism the pipeline is built on. `status_note`
already states it editorially where upstream knows; everything else is a consumer
comparison against `application_deadline`.

The ~158 plausible `date_of_expiration` values give a free disagreement report
against `programme_valid_until` — a cross-check, not ground truth.

## Order of work

1. Candidate extractor + `legal_basis` windower, with the three regex defects
   fixed. No GPU.
2. Windower recall on 40 hand-marked `legal_basis` rows. This gates everything
   that follows.
3. `deadline_model` rules on all 2500 rows. Cheap, high coverage, and it gates
   the date.
4. Stratified 120-row gold set as XFLB fragments, negatives included.
5. Baseline score, then the model service — reusing `keyword_segmenter`'s
   `modal_app.py` skeleton: `MODELS` registry, vLLM subprocess on loopback,
   `temperature=0`, `compile`/`score`, dedupe plus `ThreadPoolExecutor`. Longer
   inputs mean `--max-model-len` has to rise.

## If this ships

Same shape as `keyword_segmenter`'s note. Not `process.py` (transforms only), not
`collect()` (one export, no database). Materialise in history keyed by
`md5(source_text)`, mark the columns `inferred` in an `ORIGIN` map surfaced
through `describe()` and the DCAT table schema, and enforce the span invariant
plus the `rolling ⇒ null` rule as pandera checks on the published columns.
