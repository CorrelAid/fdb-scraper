"""Draw the fixed sample that gets hand-labelled, and only that sample.

Deterministic by construction: a seeded permutation over programmes sorted by
``id_url``, so the same 120 rows come back on any machine and a later rerun can
be diffed against ``labels.jsonl`` rather than re-labelled.

The split is decided here, not at eval time. ``train`` feeds the DSPy optimiser;
``test`` is never shown to prompt search, because a number measured on the rows
the demos were selected from is not a number you can publish.

    uv run python services/keyword_segmenter/sample_gold.py > .../sample.jsonl
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import polars as pl

from fdb_scraper import scrape
from segment.tokens import tokenize

SEED = 20260729
N_TRAIN = 70
N_TEST = 50
EXPORT_DIR = "data/foerderprogramme_export"


def sample() -> list[dict]:
    df = scrape(["url", "keywords"], export_dir=EXPORT_DIR)
    rows = (
        df.drop_nulls("keywords")
        .with_columns(
            # id_url is derived in process(); the slug is enough to sort on here
            # and keeps this script independent of the processing pipeline.
            pl.col("url")
            .str.split("Foerderprogramm/")
            .list.last()
            .alias("slug")
        )
        .sort("slug")
        .select("slug", "keywords")
        .to_dicts()
    )

    picked = random.Random(SEED).sample(rows, N_TRAIN + N_TEST)
    return [
        {
            "slug": r["slug"],
            "keywords": r["keywords"],
            "tokens": tokenize(r["keywords"]),
            "split": "train" if i < N_TRAIN else "test",
        }
        for i, r in enumerate(picked)
    ]


if __name__ == "__main__":
    for row in sample():
        print(json.dumps(row, ensure_ascii=False))
