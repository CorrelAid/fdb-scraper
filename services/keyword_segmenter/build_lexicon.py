"""Build ``segment/lexicon.json``: multi-token terms the model should never split.

The tagger's blind spot is long spans -- gold contains ten three-token terms, two
of four and one of five, so a seven-token agency name has no precedent in
training. But the export already knows those names: ``contact_info_institution``
holds 576 of them, written out in full. Nothing has to be learned from 120 rows
that another column states outright.

Two sources, both already in the dataset:

``contact_info_institution``
    Institution names, split on the author's own commas so a name that enumerates
    two departments does not become one term. Leading articles are stripped
    because the keywords field omits them ("Der Beauftragte der Bundesregierung
    ..." appears there as "Beauftragte der ..."), and a trailing bracketed
    abbreviation is kept as a second variant since the keywords field sometimes
    carries it as a bare token.

``keywords`` fields that use a delimiter
    552 rows separate their keywords with commas or semicolons, which makes their
    boundaries free. Only fields of three or more tokens are taken -- shorter ones
    are the model's strongest class already -- and only when German morphology
    supports reading the field as a single term, reusing the same guard as the
    distant-supervision set. Without that guard a comma field is as often several
    keywords as one, and the lexicon would teach over-joining.

    uv run python services/keyword_segmenter/build_lexicon.py [export.csv]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from segment.dataset import _reads_as_one_keyword  # noqa: E402
from segment.postprocess import FUNCTION_WORDS, MIN_LEXICON_TOKENS  # noqa: E402
from segment.tokens import _EDGE  # noqa: E402

HERE = Path(__file__).parent
DEFAULT_EXPORT = Path("dist/data/programme.csv")
OUT = HERE / "segment" / "lexicon.json"

_FIELD = re.compile(r"[;,/]")
_TRAILING_ABBREVIATION = re.compile(r"\s*\([^)]*\)\s*$")
_LEADING_ARTICLE = re.compile(r"^(der|die|das|die/der|der/die)\s+", re.IGNORECASE)
# The export marks lost characters with "?"; a term containing one is corrupt.
_CORRUPT = "?"


def _tokens(value: str) -> list[str]:
    return [t for token in value.split() if (t := token.strip(_EDGE))]


def _usable(tokens: list[str]) -> bool:
    """Long enough to be worth a rule, and not just function words strung together."""
    if len(tokens) < MIN_LEXICON_TOKENS:
        return False
    if any(_CORRUPT in t for t in tokens):
        return False
    content = sum(1 for t in tokens if t.lower() not in FUNCTION_WORDS)
    return content >= 2


def institution_terms(values: list[str]) -> set[tuple[str, ...]]:
    out: set[tuple[str, ...]] = set()
    for value in values:
        for field in _FIELD.split(value):
            field = field.strip()
            if not field:
                continue
            # With and without the bracketed abbreviation: the keywords field uses
            # both forms ("... Kultur und Medien BKM").
            for variant in (field, _TRAILING_ABBREVIATION.sub("", field)):
                tokens = _tokens(_LEADING_ARTICLE.sub("", variant))
                if _usable(tokens):
                    out.add(tuple(tokens))
    return out


def delimited_terms(values: list[str]) -> set[tuple[str, ...]]:
    out: set[tuple[str, ...]] = set()
    for value in values:
        fields = [f.strip() for f in re.split(r"[;,]", value) if f.strip()]
        if len(fields) < 2:
            continue
        for field in fields:
            tokens = _tokens(field)
            # `_reads_as_one_keyword` is the same guard the distant-supervision
            # set uses; four-token-plus fields are dropped there as "certainly
            # several keywords", and the same reasoning applies here.
            if len(tokens) == MIN_LEXICON_TOKENS and _usable(tokens):
                if _reads_as_one_keyword(tokens):
                    out.add(tuple(tokens))
    return out


def main() -> None:
    import polars as pl

    export = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXPORT
    df = pl.read_csv(export, infer_schema_length=0)
    institutions = institution_terms(df["contact_info_institution"].drop_nulls().to_list())
    delimited = delimited_terms(
        [v for v in df["keywords"].drop_nulls().to_list() if v.strip()]
    )
    entries = sorted(institutions | delimited, key=lambda e: (-len(e), e))

    OUT.write_text(
        json.dumps(
            {
                "source": str(export),
                "entries": [list(e) for e in entries],
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n"
    )
    print(
        f"{len(entries)} entries -> {OUT}"
        f"  ({len(institutions)} from institutions, {len(delimited)} from delimited keywords)"
    )


if __name__ == "__main__":
    main()
