"""Exact-span boundary F1: the number that decides whether this ships.

A predicted keyword counts only when *both* its boundaries match the gold
partition. Partial credit would flatter the model on exactly the failure that
matters -- "Beauftragte der Bundesregierung fuer Kultur und Medien" split into
six terms overlaps the gold span heavily while being six pieces of garbage.

Spans, not strings: a keyword can legitimately repeat inside one value, so the
comparison is over multisets of token index ranges.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from segment.tokens import is_partition, spans


@dataclass(frozen=True)
class Score:
    precision: float
    recall: float
    f1: float
    support: int  # gold terms, so several Scores can be pooled by weight

    @property
    def as_row(self) -> dict[str, float]:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "support": self.support,
        }


def score_one(pred: list[int], gold: list[int], n_tokens: int) -> Score:
    """Span P/R/F1 for one keywords value.

    An invalid partition scores zero rather than raising: the service falls back
    to a single term in that case, and the metric has to price that outcome the
    same way the consumer would experience it.
    """
    gold_spans = Counter(spans(gold))
    if not is_partition(pred, n_tokens):
        return Score(0.0, 0.0, 0.0, sum(gold_spans.values()))

    pred_spans = Counter(spans(pred))
    hits = sum((pred_spans & gold_spans).values())
    precision = hits / sum(pred_spans.values())
    recall = hits / sum(gold_spans.values())
    f1 = 0.0 if hits == 0 else 2 * precision * recall / (precision + recall)
    return Score(precision, recall, f1, sum(gold_spans.values()))


def pool(scores: list[Score]) -> Score:
    """Micro-average over a dataset.

    Micro, not macro: a 25-keyword value should weigh more than a 2-keyword one,
    because the published column is a flat list of terms and that is what a
    consumer sees.
    """
    support = sum(s.support for s in scores)
    if not support:
        return Score(0.0, 0.0, 0.0, 0)
    # Reconstruct hit/total counts from the per-row rates to avoid threading
    # three more fields through Score.
    hits = sum(s.recall * s.support for s in scores)
    pred_total = sum(s.recall * s.support / s.precision for s in scores if s.precision)
    precision = hits / pred_total if pred_total else 0.0
    recall = hits / support
    f1 = 0.0 if hits == 0 else 2 * precision * recall / (precision + recall)
    return Score(precision, recall, f1, support)



def sweep_threshold(
    probs_by_row: list[list[float]],
    gold_by_row: list[list[int]],
    grid: list[float] | None = None,
) -> list[tuple[float, Score]]:
    """Score every candidate join threshold against one split.

    The point of taking probabilities rather than booleans: a spurious join
    destroys two gold keywords and invents a wrong one, while a caught compound
    wins one, so the optimum sits well above 0.5. Rather than argue about where,
    fit it -- and because the probabilities come from a single call per row, the
    whole grid costs no extra inference.

    Returns (threshold, pooled score) for each point, in grid order.
    """
    from segment.tokens import joins_to_sizes

    grid = grid or [i / 20 for i in range(1, 20)]
    out = []
    for tau in grid:
        scored = []
        for probs, gold in zip(probs_by_row, gold_by_row):
            n_tokens = sum(gold)
            if not probs:
                # No usable answer: the service falls back to one keyword per
                # token, so the sweep has to price that, not skip the row.
                pred = [1] * n_tokens
            else:
                pred = joins_to_sizes([p >= tau for p in probs])
            scored.append(score_one(pred, gold, n_tokens))
        out.append((tau, pool(scored)))
    return out
