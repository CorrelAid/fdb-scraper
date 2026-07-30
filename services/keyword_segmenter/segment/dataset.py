"""Training data for per-word keyword-boundary tagging.

The label is BIO over *words*: each whitespace token is either ``B`` (starts a new
keyword) or ``I`` (continues the previous one). That is the same decision as the
per-gap join used by the prompted models -- gap ``i`` is a join exactly when word
``i+1`` is ``I`` -- but expressed the way an encoder is normally fine-tuned, so the
alignment to subwords is standard rather than bespoke.

Two sources:

``gold``
    The 120 hand-labelled rows. Small (309 gaps, 32 joins) but trustworthy.

``distant``
    Rows whose author used commas or semicolons. Strip the delimiter and the
    boundaries are recoverable for free -- but **not naively**: 15% of delimited
    fields are four tokens or longer, and those are almost always several keywords
    that simply had no comma between them ("Zuschuss Kompetenz Unternehmensführung
    Geschäftsmodell ..." is one comma-field and nine keywords). Taking every field
    as one keyword yields a 65.5% join rate against a true rate of 10.4%, which
    would train precisely the over-joining bias that already dominates.

    So the fields are filtered: 4+ tokens dropped, and a multi-token field is only
    believed to be a single keyword when German morphology supports it -- an
    inflected adjective, a shared-prefix ellipsis, or a bracketed abbreviation.
    Everything else contributes its unambiguous split at the delimiter and nothing
    else.
"""

from __future__ import annotations

import re

from segment.tokens import sizes_to_joins, tokenize

B, I = 0, 1

# -e/-er/-es/-em: the attributive endings. -en is excluded because it is also the
# verb infinitive, and "bürgen Betriebsmittel" is a verb beside a noun, not an
# adjective agreeing with one.
_INFLECTED = re.compile(r"(e|er|es|em)$")
_DELIMITER = re.compile(r"[;,]")
MAX_FIELD_TOKENS = 3


def _reads_as_one_keyword(tokens: list[str]) -> bool:
    """Whether a short delimited field is plausibly a single keyword.

    Conservative on purpose: a false "join" here teaches the model the exact error
    it is already most prone to.
    """
    if len(tokens) < 2:
        return True
    first = tokens[0]
    if first.endswith("-"):  # land- und forstwirtschaftlichen Wege
        return True
    if tokens[-1].startswith("("):  # Digitaler Produktpass (DPP)
        return True
    if first.isalpha() and _INFLECTED.search(first.lower()):
        return True  # inflected adjective agreeing with what follows
    return False


def labels_for(tokens: list[str], sizes: list[int]) -> list[int]:
    """BIO labels from a partition. The first word is always ``B``."""
    return [B] + [I if join else B for join in sizes_to_joins(sizes)]


def gold(rows: list[dict], split: str) -> list[dict]:
    return [
        {"tokens": r["tokens"], "labels": labels_for(r["tokens"], r["group_sizes"]), "source": "gold"}
        for r in rows
        if r["split"] == split
    ]


def distant(values: list[str], exclude: set[str] | None = None) -> list[dict]:
    """Recover labels from delimiter-using rows.

    ``exclude`` should hold every raw ``keywords`` value in the held-out split, so
    a row cannot reach training through this back door.
    """
    exclude = exclude or set()
    out = []
    for value in values:
        if value in exclude or not _DELIMITER.search(value):
            continue
        fields = [f.strip() for f in _DELIMITER.split(value) if f.strip()]
        if len(fields) < 2:
            continue

        tokens: list[str] = []
        labels: list[int] = []
        usable = True
        for field in fields:
            words = field.split()
            if not words:
                continue
            if len(words) > MAX_FIELD_TOKENS:
                # Certainly several keywords; the field carries no reliable
                # internal labels, so the whole row is dropped rather than
                # guessed at.
                usable = False
                break
            inside = _reads_as_one_keyword(words)
            tokens.extend(words)
            # First word of a field always starts a keyword. The rest continue it
            # only when morphology says the field really is one term; otherwise
            # each word stands alone.
            labels.append(B)
            labels.extend([I if inside else B] * (len(words) - 1))
        if usable and len(tokens) > 1:
            out.append({"tokens": tokens, "labels": labels, "source": "distant"})
    return out


def stats(examples: list[dict]) -> dict:
    words = sum(len(e["labels"]) for e in examples)
    inside = sum(sum(1 for x in e["labels"] if x == I) for e in examples)
    return {
        "rows": len(examples),
        "words": words,
        "I_labels": inside,
        "I_rate": round(inside / words, 4) if words else 0.0,
    }
