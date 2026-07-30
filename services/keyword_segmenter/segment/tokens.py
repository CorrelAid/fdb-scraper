"""Tokenisation and the partition contract the model is held to.

The upstream ``keywords`` field is one string per programme holding several
keywords with no reliable separator: 95% of non-null values are joined by
spaces alone, and multi-word keywords are indistinguishable from separate ones
without reading the German. See notebooks/E12.

Rather than have a model emit keyword *strings* -- which cannot be checked
against anything -- it emits a **partition of the whitespace tokens**: a list of
group sizes that must sum to the token count. That makes invention structurally
impossible (every term is a contiguous span of the source) and reduces the
unverifiable part of the task to boundary placement alone, which is what
``metric`` measures.

``scraper._clean`` has already collapsed whitespace runs and stripped the ends,
so ``str.split`` is exact: no empty tokens, no information lost between them.
"""

from __future__ import annotations

# Punctuation carried on a token's edge is a boundary cue for the model, so it
# stays on the token and is only stripped from the term that gets published.
_EDGE = " ,;.:()[]\"'"


def tokenize(keywords: str) -> list[str]:
    """Whitespace tokens of a raw ``keywords`` value, punctuation attached."""
    return keywords.split()


def spans(sizes: list[int]) -> list[tuple[int, int]]:
    """Turn group sizes into half-open token index ranges.

    The spans -- not the strings -- are what :mod:`metric` compares, because two
    different partitions can produce the same term twice ("Sprache" appears
    twice in one real value) and a set of strings would silently merge them.
    """
    out, start = [], 0
    for size in sizes:
        out.append((start, start + size))
        start += size
    return out


def is_partition(sizes: list[int], n_tokens: int) -> bool:
    """Whether ``sizes`` covers exactly ``n_tokens`` with no empty group.

    This is the whole verifiable contract: every group is non-empty, every token
    belongs to exactly one group, and nothing outside the source appears. A
    model reply failing it is retried, then falls back to one term.
    """
    return bool(sizes) and all(s >= 1 for s in sizes) and sum(sizes) == n_tokens


def terms(tokens: list[str], sizes: list[int]) -> list[str]:
    """The published keyword list for a validated partition."""
    return [
        term
        for lo, hi in spans(sizes)
        # A group of pure punctuation strips to nothing; dropping it keeps the
        # output clean without making the partition itself invalid.
        if (term := " ".join(tokens[lo:hi]).strip(_EDGE))
    ]


def joins_to_sizes(joins: list[bool]) -> list[int]:
    """Turn per-gap join decisions into group sizes.

    ``joins[i]`` answers "does token i belong with token i+1?", so a value with
    ``n`` tokens has exactly ``n-1`` gaps. Any list of that length maps to a valid
    partition -- there is no arithmetic for the model to get wrong, which is the
    entire reason for preferring this over asking for sizes directly. Asking for
    sizes made a quarter of replies unusable: they were well-formed lists of
    integers that simply did not sum to the token count.
    """
    sizes = [1]
    for join in joins:
        if join:
            sizes[-1] += 1
        else:
            sizes.append(1)
    return sizes


def sizes_to_joins(sizes: list[int]) -> list[bool]:
    """Inverse of :func:`joins_to_sizes`, for turning gold labels into demos."""
    joins: list[bool] = []
    for size in sizes:
        joins.extend([True] * (size - 1))
        joins.append(False)
    return joins[:-1]  # the final group has no gap after it
