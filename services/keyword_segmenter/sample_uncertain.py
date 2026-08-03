"""Draw a second labelling sample where the model is least sure of itself.

The first sample was random, which is what makes the held-out 50 a publishable
number. It is also why the training half is 93% single-token terms and its longest
span is five tokens: a random draw over this column mostly asks the easy question.
The measured consequence is that terms of six tokens or more had no precedent at
all, and the seven-token agency names came back split.

So this draw is deliberately not random. Two criteria, both aimed at the gap the
first sample left:

**Uncertainty.** A row is a candidate when at least one of its gaps scores between
0.2 and 0.8. Those are the decisions the model would get right or wrong on a coin
flip, and they are where a label buys the most -- a row it already scores at 0.001
teaches nothing.

**One targeted pattern**, because uncertainty alone provably misses the failure that
prompted this round. ``Erhaltung polnische Sprache`` is one keyword split at
**p=0.016** -- confident, not undecided, and therefore invisible to the criterion
above. What the model is doing there is visible in its own numbers: it joins the
adjective to its noun (0.940) and cuts the noun in front of them (0.098), i.e. it
found a two-token compound and did not consider that the word before it belongs to
the same term. So the second criterion looks for that shape directly: a confident
join between a lowercase inflected adjective and a capitalised noun, with a
confident cut immediately before it. No labels are needed to find it, only the
model's own probabilities, and it selects for the exact decision the training data
never taught -- three-token terms, of which gold holds ten.

**Length strata.** Candidates are bucketed by token count and drawn evenly across
buckets, because uncertainty alone would return mostly the same short rows the first
sample already covers. The long buckets are the point.

Rows already in ``sample.jsonl`` are excluded, so the held-out 50 cannot leak in
here, and everything drawn is labelled ``train``: a sample selected using the model's
own probabilities cannot also measure it.

**Prose is excluded**, and that exclusion is the one judgement call in this script.
A few dozen values are not keyword lists at all but sentences -- "Austausch- und
Vernetzungsmaßnahmen, die die Stärkung einer diversen Wissenschaftslandschaft und
Kommunikationskultur zum Ziel haben". There is no defensible keyword partition of a
relative clause: every candidate is taste, and taste labelled as gold would train the
one failure that already dominates, over-joining. They are detected by function-word
density inside the author's own fields and skipped, which is a stated limit of the
column rather than a silent one: whatever the model does to those rows is unmeasured.

    uv run --extra tagger python services/keyword_segmenter/sample_uncertain.py \\
        > services/keyword_segmenter/sample_uncertain.jsonl
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

HERE = Path(__file__).parent
SEED = 20260803
EXPORT = Path("dist/data/programme.csv")
# Gaps this uncertain are the ones a label actually decides.
LOW, HIGH = 0.2, 0.8
# (lowest token count, how many rows to draw). Weighted towards the long tail: the
# short buckets are already covered by 120 random rows, the long ones are empty.
STRATA = ((2, 8), (5, 12), (10, 14), (20, 16))
TARGET = sum(n for _, n in STRATA)
# How many rows to draw for the targeted pattern, on top of the strata.
N_PATTERN = 14
# Confident enough that the row is not already in the uncertain pool.
CONFIDENT_JOIN, CONFIDENT_CUT = 0.8, 0.2
# -e/-er/-es/-em, the attributive endings; -en is excluded because it is also the
# verb infinitive. Same rule as segment.dataset, for the same reason.
_INFLECTED = re.compile(r"(e|er|es|em)$")
# Reported failures, drawn regardless of what the criteria above select. This one
# came from a demo notebook: "Erhaltung polnische Sprache" and "Pflege polnischer
# Sprache" are single keywords, split at p=0.016 and p=0.098.
KNOWN_FAILURES = ("Erhaltung polnische Sprache",)


def _slug(url: str) -> str:
    return url.split("Foerderprogramm/")[-1]


def is_prose(value: str) -> bool:
    """Whether any of the author's own fields reads as a clause rather than a term.

    Three function words in one field is the threshold: keyword phrases in this
    column reach two ("Urlaub auf dem Bauernhof", "Beauftragte der Bundesregierung
    für Kultur"), sentences run well past it.
    """
    from segment.postprocess import FUNCTION_WORDS
    from segment.tokens import _EDGE

    for field in re.split(r"[;,]", value):
        words = [w.strip(_EDGE).lower() for w in field.split()]
        if sum(1 for w in words if w in FUNCTION_WORDS) >= 3:
            return True
    return False


def _compound_the_model_cut_short(tokens: list[str], gaps: list[float]) -> list[int]:
    """Gaps where a confident two-token compound may really be a three-token term.

    Returns the index of each cut that sits immediately before an
    inflected-adjective + noun pair the model joined confidently.
    """
    out = []
    for i in range(1, len(gaps)):
        adjective, noun = tokens[i], tokens[i + 1]
        if (
            gaps[i] >= CONFIDENT_JOIN
            and gaps[i - 1] <= CONFIDENT_CUT
            and adjective[:1].islower()
            and adjective.isalpha()
            and len(adjective) > 3
            and _INFLECTED.search(adjective)
            and noun[:1].isupper()
        ):
            out.append(i - 1)
    return out


def candidates() -> list[dict]:
    import polars as pl

    from segment.tagger import KeywordTagger
    from segment.tokens import tokenize

    already = {
        json.loads(line)["keywords"]
        for line in (HERE / "sample.jsonl").read_text().splitlines()
    }
    df = pl.read_csv(EXPORT, infer_schema_length=0).drop_nulls("keywords")
    rows: dict[str, str] = {}
    for value, url in zip(df["keywords"].to_list(), df["url"].to_list()):
        if value.strip() and value not in already and value not in rows:
            named = any(marker in value for marker in KNOWN_FAILURES)
            # The threshold counts function words, so a long agency name reaches it
            # on "der ... für ... und" alone -- which is why a row named as a failure
            # is never filtered out by it. Loosening the threshold instead would pull
            # in clauses, which is the thing worth avoiding.
            if is_prose(value) and not named:
                continue
            rows[value] = _slug(url or "")

    tagger = KeywordTagger()
    out = []
    values = list(rows)
    for start in range(0, len(values), 32):
        chunk = [v for v in values[start : start + 32] if len(tokenize(v)) > 1]
        if not chunk:
            continue
        tokenised = [tokenize(v) for v in chunk]
        for value, tokens, probs in zip(
            chunk, tokenised, tagger._probabilities(tokenised)
        ):
            gaps = probs[1 : len(tokens)]
            uncertain = [i for i, p in enumerate(gaps) if LOW <= p <= HIGH]
            pattern = _compound_the_model_cut_short(tokens, gaps)
            if uncertain or pattern:
                out.append({
                    "slug": rows[value],
                    "keywords": value,
                    "tokens": tokens,
                    "split": "train",
                    "source": "uncertainty" if uncertain else "compound-pattern",
                    # Kept for the labelling worksheet, and dropped by build_labels:
                    # a probability from the model being corrected has no business
                    # in the labels it is corrected against.
                    "gap_probs": [round(p, 3) for p in gaps],
                    "uncertain_gaps": uncertain,
                    "pattern_gaps": pattern,
                })
    return out


def stratified(pool: list[dict]) -> list[dict]:
    """Draw evenly across length strata, plus the targeted pattern. Deterministic."""
    rng = random.Random(SEED)
    bounds = [lo for lo, _ in STRATA] + [10**9]
    picked: list[dict] = []
    uncertain_pool = [r for r in pool if r["uncertain_gaps"]]
    for (lo, want), hi in zip(STRATA, bounds[1:]):
        bucket = sorted(
            (r for r in uncertain_pool if lo <= len(r["tokens"]) < hi),
            key=lambda r: r["keywords"],
        )
        picked += rng.sample(bucket, min(want, len(bucket)))
        if len(bucket) < want:
            print(
                f"stratum {lo}+: only {len(bucket)} candidates for {want}",
                file=sys.stderr,
            )

    # The targeted rows are drawn from the whole pattern pool rather than per
    # stratum: there is only one decision shape being chased, and where it occurs is
    # not something to balance away.
    chosen = {r["keywords"] for r in picked}
    pattern_pool = sorted(
        (r for r in pool if r["pattern_gaps"] and r["keywords"] not in chosen),
        key=lambda r: r["keywords"],
    )
    picked += rng.sample(pattern_pool, min(N_PATTERN, len(pattern_pool)))
    print(
        f"{len(pattern_pool)} rows carry the compound pattern, {N_PATTERN} drawn",
        file=sys.stderr,
    )

    # The rows a human actually caught the model on, included by name. A sampling
    # criterion is a proxy for "where is this wrong"; a reported failure is the
    # thing itself, and leaving it to chance would be perverse.
    chosen = {r["keywords"] for r in picked}
    for row in pool:
        if row["keywords"] not in chosen and any(
            marker in row["keywords"] for marker in KNOWN_FAILURES
        ):
            picked.append(row | {"source": "known-failure"})
    return sorted(picked, key=lambda r: r["keywords"])


if __name__ == "__main__":
    pool = candidates()
    print(f"{len(pool)} uncertain rows in the corpus", file=sys.stderr)
    for row in stratified(pool):
        print(json.dumps(row, ensure_ascii=False))
