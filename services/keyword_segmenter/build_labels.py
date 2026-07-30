"""Hand-labelled gold partitions for ``sample.jsonl``.

Only rows containing at least one multi-token keyword are listed in ``GOLD``;
everything else is one keyword per whitespace token, which is the majority. That
asymmetry is the point of the ``all-ones`` baseline in :mod:`eval` -- a model has
to beat "never join anything" to be worth running at all.

Labelling rules applied, in order of precedence:

1. An established German compound term written with spaces is one keyword:
   ``Ländliche Entwicklung``, ``Erneuerbare Energien``, ``sozialer Wohnungsbau``,
   ``künstliche Befruchtung``, ``bürgerschaftliches Engagement``.
2. A proper name or programme title is one keyword, including its abbreviation
   in brackets: ``Hochschulen für Angewandte Wissenschaften (HAW)``,
   ``Zentrales Innovationsprogramm Mittelstand``, ``Just Transition Fund``,
   ``Natura 2000``, ``Interreg VI``, ``Industrie 4.0``, ``Programmteil 1``.
3. A shared-prefix ellipsis binds to its continuation:
   ``land- und forstwirtschaftlichen Wege``, ``Gewerbe- und Militärbrachen``.
4. A trailing comma ends a keyword -- the only delimiter that is honoured, and
   only where the author used it consistently (row 74's Smart City list).
5. Otherwise a token stands alone. Adjacent nouns that merely share a topic are
   separate keywords, which is the common case: ``Waldgebiet Waldfläche Forst``
   is three, not one.

Ambiguity was resolved toward *splitting*: an over-split term is a recoverable
search miss, a wrongly-joined one is a term no consumer will ever match.

    uv run python services/keyword_segmenter/build_labels.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent

# Row index in sample.jsonl -> group sizes over its whitespace tokens.
GOLD: dict[int, list[int]] = {
    0: [1, 1, 2, 1],  # Ländliche Entwicklung
    11: [1, 1, 1, 1, 3, 1, 1, 1, 1, 1],  # Schaffung neuer Wohnungen
    15: [1, 1, 1, 1, 1, 1, 1, 1, 2, 1],  # Ländlicher Raum
    19: [1, 2, 1, 1, 1, 1, 1, 1, 1, 1],  # Kommunales Bildungsmanagement
    23: [1, 1, 1, 1, 1, 2, 1, 1],  # bürgerschaftliches Engagement
    34: [1, 1, 1, 1, 1, 1, 2],  # Natura 2000
    36: [1, 3, 1, 1],  # Freiwilliges Ökologisches Jahr
    37: [1, 2, 1, 1, 1, 1, 1, 1, 1, 3],  # ZIM-Phase 1; Zentrales Innovationsprogramm Mittelstand
    39: [1, 1, 1, 1, 1, 3, 1, 1, 1, 1, 1, 1, 1],  # Herz Kreislauf Erkankung
    40: [5, 1, 1, 1, 2, 2, 2, 4, 1, 1, 1, 1, 2, 2, 1, 1, 1, 1, 1, 2, 1, 1, 2],
    53: [1, 1, 1, 1, 1, 2],  # künstliche Befruchtung
    54: [2, 2, 1, 1, 1],  # mittelständische Unternehmen; freie Berufe
    55: [3, 1, 1, 1, 1, 1],  # Just Transition Fund
    57: [1, 1, 2, 1, 1, 1],  # Erneuerbare Energien
    66: [1, 1, 1, 1, 1, 1, 3, 1, 1, 1],  # Gewerbe- und Militärbrachen
    71: [2, 2, 1, 1, 1, 1, 1, 3, 1, 2],  # Digitaler Produktpass (DPP); Digitale Zwillinge
    74: [1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 1],  # the comma-delimited Smart City list
    81: [1] * 14 + [2] + [1, 1, 1],  # Programmteil 1
    86: [1, 2, 1, 1, 1, 1, 1, 1],  # sozialer Wohnungsbau
    88: [1, 1, 1, 1, 1, 2, 1, 1, 2, 2],  # Interreg VI; soziale Inklusion; bessere Grenzregion
    89: [1, 2, 1, 1, 1, 1, 1, 3, 1, 1],  # KI-basierte Robotik; verkörperte Künstliche Intelligenz
    90: [1, 1, 4],  # land- und forstwirtschaftlichen Wege
    100: [1, 2, 2, 1, 1, 1],  # psychische Gesundheit; Digital Natives
    102: [1, 1, 1, 1, 1, 2],  # Industrie 4.0
    109: [2, 1, 1, 1, 3],  # behindertenpolitisches maßnahmenpaket; menschen mit behinderung
    114: [1, 1, 3, 1],  # Forschung und Entwicklung
    117: [1, 1, 1, 1, 2, 1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1],
}


def main() -> None:
    rows = [json.loads(line) for line in (HERE / "sample.jsonl").read_text().splitlines()]
    out = []
    for i, row in enumerate(rows):
        sizes = GOLD.get(i, [1] * len(row["tokens"]))
        # A gold label that is not a partition is a labelling slip, not input
        # data -- fail loudly rather than silently score against nonsense.
        if sum(sizes) != len(row["tokens"]):
            raise SystemExit(
                f"row {i}: sizes sum to {sum(sizes)}, expected {len(row['tokens'])}\n"
                f"  {' '.join(row['tokens'])}"
            )
        out.append(row | {"group_sizes": sizes})

    (HERE / "labels.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out)
    )
    joined = sum(1 for r in out if any(s > 1 for s in r["group_sizes"]))
    print(f"{len(out)} labelled, {joined} contain a multi-token keyword")


if __name__ == "__main__":
    main()
