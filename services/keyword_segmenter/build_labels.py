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

Two rules were added for the second sample (``GOLD_UNCERTAIN``), because the first
sample never forced the question and the model's failures were exactly there:

6. A nominalisation takes its complement with it: ``Erhaltung polnische Sprache``,
   ``Pflege polnischer Sprache``, ``Stärkung der ländlichen Räume``,
   ``Stilllegung von Kraftwerken``. Rule 5 would split all four, and the model does
   -- at p=0.016 for the first, which is a confident error rather than a coin flip.
7. A function word is never a boundary: an article, preposition or conjunction
   belongs inside a keyword, never at its edge. This mirrors ``postprocess`` exactly,
   which is not a coincidence but a requirement -- a rule that fires at inference and
   a label that disagrees with it would make the measurement meaningless. Asserted
   both ways in the test suite.

Ambiguity was resolved toward *splitting*: an over-split term is a recoverable
search miss, a wrongly-joined one is a term no consumer will ever match.

    uv run python services/keyword_segmenter/build_labels.py
"""

from __future__ import annotations

import json
from collections import Counter
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


# --- The second sample: uncertainty-drawn, train only -------------------------
#
# Keyed on slug rather than row index, and written as spans rather than sizes. Both
# choices are about being able to re-draw: the draw depends on the model's own
# probabilities, so a retrain reshuffles which rows come back, and index-keyed sizes
# would silently re-point at different keywords. A span is (first token, how many),
# every token not covered stands alone, and `main` checks the arithmetic.
#
# All of these are `train`. A sample selected by asking the model where it is unsure
# cannot also be used to measure it -- the held-out 50 stays the untouched random
# draw from sample.jsonl, so the published numbers remain comparable across rounds.
GOLD_UNCERTAIN: dict[str, list[tuple[int, int]]] = {
    'Bund/BKM/bundesfoerderung-deutsch-polnischer-vertrag.html': [(0, 2), (2, 2), (4, 2), (6, 2), (8, 2), (10, 2), (12, 2), (18, 2), (20, 2), (22, 3), (25, 3), (28, 2), (30, 8)],
    'Bund/BKM/jurybasierte-kulturelle-produktion-kurzfilm.html': [(2, 3)],
    'Bund/BMBF/bmbf-interdisziplinaere-ansaetze-schmerz.html': [(3, 6), (9, 2), (11, 2), (13, 2), (15, 3), (19, 2), (24, 2)],
    'Bund/BMBF/bmbf-neurobiologisch-inspirierte-ki.html': [(0, 2), (2, 3), (7, 2)],
    'Bund/BMBF/bmftr-postdoc-starting-grants.html': [(0, 2), (3, 2), (5, 2), (8, 3)],
    'Bund/BMBF/datengestuetzte-loesungen-reale-herausforderungen.html': [(1, 7), (8, 2), (10, 3)],
    'Bund/BMBF/deutsch-ukrainisch-forschungspartnerschaften.html': [(4, 3)],
    'Bund/BMBF/kmu-innovativ-biooekonomie.html': [(2, 2)],
    'Bund/BMBF/massnahmen-resilienter-versorgung.html': [(1, 3), (13, 2), (16, 2), (22, 3)],
    'Bund/BMBF/materialvital-modul-1.html': [(11, 3), (17, 2), (22, 2)],
    'Bund/BMBF/praevention-darmkrebs-juengere-kuenftige.html': [(2, 2)],
    'Bund/BMBF/transfer-netzintegration-quantenkommunikation.html': [(14, 3), (25, 2)],
    'Bund/BMBF/ueberbetriebliche-berufsbildungsstaetten-bmbf-bibb.html': [(3, 2)],
    'Bund/BMBF/verhuetungsmittelforschung-alle-geschlechter.html': [(0, 5), (8, 2), (10, 2)],
    'Bund/BMEL/organisation-wasserbereitstellung-gartenbau.html': [(3, 2)],
    'Bund/BMG/neue-versorgungsformen-in-der-gkv.html': [(0, 2), (2, 2), (6, 2), (8, 3)],
    'Bund/BMG/themenoffene-foerderung-gkv-einstufig-lang.html': [(3, 2), (6, 2), (11, 2), (17, 2), (22, 3)],
    'Bund/BMU/e-auto-foerderung.html': [(2, 2), (31, 3)],
    'Bund/BMU/ko-mo-na.html': [(6, 3)],
    'Bund/BMWi/bund-exportinitiative-sicherheitstechnologien.html': [],
    'Bund/BMWi/energieeffizienz-prozesswaerme-zuschuss-4-premium.html': [(2, 2)],
    'Bund/BMWi/erp-foerderkredit-digitalisierung-865244.html': [],
    'Bund/BMWi/german-accelerator-program.html': [(4, 2), (6, 2), (8, 2)],
    'Bund/BMWi/geschaeftsmodelle-und-pionierloesungen-igp.html': [(5, 2), (7, 3), (11, 2), (13, 2), (15, 2), (17, 2)],
    'Bund/BMWi/high-tech-gruenderfonds-bund.html': [(0, 2), (4, 3), (8, 2), (10, 2), (12, 2), (19, 2), (21, 3)],
    'Bund/BMWi/ipcei-ki-867319.html': [(2, 2), (4, 3), (8, 2), (10, 2), (13, 2), (16, 3), (19, 3), (22, 2)],
    'Bund/BMWi/stark.html': [(0, 2), (3, 2)],
    'Bund/BMWi/zim-unternehmen-in-innovationsnetzwerken.html': [(8, 2), (10, 3)],
    'Bund/KfW/konsortialkredit-nachhaltige-transformation.html': [(0, 2)],
    'Bund/KultStiftBund/WAYS.html': [(2, 2), (7, 2), (10, 3), (13, 2), (15, 3), (19, 2), (23, 3)],
    'Land/Baden-Wuerttemberg/erweiterung-von-innovationskapazitaeten-evi-plus.html': [(3, 2), (5, 3), (8, 2)],
    'Land/Baden-Wuerttemberg/nachhaltige-waldwirtschaft-nww-866548.html': [(1, 2), (5, 2)],
    'Land/Baden-Wuerttemberg/pro-beruf-berufserprobung-ueberbetriebliche.html': [(2, 2), (4, 2), (6, 2)],
    'Land/Baden-Wuerttemberg/vwv-fakt-agrarumwelt-klimaschutz-tierwohl.html': [(8, 2), (12, 2)],
    'Land/Baden-Wuerttemberg/zuwendungen-naturparke-863269.html': [(5, 4), (9, 2), (21, 2), (23, 3)],
    'Land/Bayern/bayerisches-technologiefoerderungs-programm-plus.html': [(2, 2)],
    'Land/Bayern/bayerisches-wohnungsbauprogramm-eigenwohnraum.html': [(3, 3)],
    'Land/Bayern/energieforschungsprogramm-bay.html': [(4, 2), (6, 2), (8, 3), (12, 2), (17, 2)],
    'Land/Bayern/innovationskredit-4-0.html': [(2, 2)],
    'Land/Bayern/kinderwunschbehandlungen.html': [(2, 2)],
    'Land/Bremen/aufstiegsfortbildungs-praemie-bremen.html': [(1, 3)],
    'Land/Bremen/innovationsdienstleistungen-zuschuesse.html': [(2, 2)],
    'Land/Hamburg/foerderrichtlinie-fuer-den-neubau-von-wohnraum.html': [(0, 2)],
    'Land/Hessen/gesundheitliche-versorgung-laendliche-raeume.html': [(2, 2)],
    'Land/NRW/kulturelle-zusammenarbeit-guetersloh.html': [(0, 3), (3, 2), (5, 4), (9, 2), (14, 2), (16, 2)],
    'Land/NRW/projekte-einrichtungen-kultur-kunst-bildung-a.html': [(4, 2), (6, 2), (8, 2)],
    'Land/NRW/rahmenrichtlinien-vertragsnaturschutz.html': [(4, 2)],
    'Land/Niedersachsen/mietwohnraum-gemeinschaftliche-wohnformen.html': [(2, 2)],
    'Land/Rheinland-Pfalz/implementierung-betrieblicher-innovationen.html': [(6, 2)],
    'Land/Saarland/regionale-wirtschaftsstruktur-grw-saarland.html': [(5, 2)],
    'Land/Saarland/richtlinie-kinderwunsch.html': [(1, 2), (7, 2)],
    'Land/Sachsen-Anhalt/richtlinie-junges-wohnen.html': [(5, 2)],
    'Land/Sachsen-Anhalt/versorgungsstrukturen-ehrenamt-und-selbsthilfe.html': [(1, 2)],
    'Land/Schleswig-Holstein/angebote-frueher-hilfen-landesprogramm-schutzengel.html': [(0, 2), (3, 2)],
    'Land/Schleswig-Holstein/arbeitsmarktprogramm-esf-plus-aktion-b1.html': [(3, 2), (10, 2)],
    'Land/Schleswig-Holstein/soziale-wohnraumfoerderung-eigentumsmassnahmen.html': [(0, 2), (6, 2)],
    'Land/Schleswig-Holstein/staedtebaufoerderung-sh.html': [(2, 2), (4, 2), (6, 2)],
    'Land/Thueringen/aquakultur-fischwirtschaft-thuerfrl-emfaf-865173.html': [(0, 2), (3, 2), (9, 3), (15, 3), (18, 2)],
    'Land/Thueringen/fti-thueringen-transfer-get-started-2gether.html': [],
    'Land/Thueringen/stipendien-kulturstiftung-thueringen.html': [(0, 2), (6, 3)],
    'Land/Thueringen/thueringer-europafoerderrichtlinie.html': [(2, 2)],
}


def sizes_from_spans(n_tokens: int, spans: list[tuple[int, int]]) -> list[int]:
    """Group sizes from multi-token spans; every uncovered token is its own keyword."""
    sizes, i = [], 0
    by_start = dict(spans)
    while i < n_tokens:
        size = by_start.get(i, 1)
        sizes.append(size)
        i += size
    return sizes


def uncertain_rows() -> list[dict]:
    """The second sample, labelled, with the sampling metadata dropped.

    ``gap_probs`` and the two gap lists exist to make the worksheet readable; a
    probability produced by the model under correction has no place in the labels it
    is corrected against, so they do not reach labels.jsonl.
    """
    path = HERE / "sample_uncertain.jsonl"
    if not path.exists():
        return []
    out = []
    for row in (json.loads(line) for line in path.read_text().splitlines()):
        spans = GOLD_UNCERTAIN.get(row["slug"])
        if spans is None:
            raise SystemExit(f"unlabelled row in the second sample: {row['slug']}")
        sizes = sizes_from_spans(len(row["tokens"]), spans)
        if sum(sizes) != len(row["tokens"]):
            raise SystemExit(
                f"{row['slug']}: spans {spans} do not partition {len(row['tokens'])} tokens\n"
                f"  {' '.join(row['tokens'])}"
            )
        out.append({
            k: v for k, v in row.items()
            if k not in {"gap_probs", "uncertain_gaps", "pattern_gaps"}
        } | {"group_sizes": sizes})
    return out


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
        out.append(row | {"source": "random", "split": row["split"]} | {"group_sizes": sizes})

    out += uncertain_rows()

    (HERE / "labels.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out)
    )
    joined = sum(1 for r in out if any(s > 1 for s in r["group_sizes"]))
    lengths = Counter(s for r in out for s in r["group_sizes"])
    print(f"{len(out)} labelled, {joined} contain a multi-token keyword")
    print(
        "  by source: "
        + ", ".join(f"{k}={v}" for k, v in Counter(r["source"] for r in out).items())
    )
    print("  term lengths: " + ", ".join(f"{k}:{lengths[k]}" for k in sorted(lengths)))


if __name__ == "__main__":
    main()
