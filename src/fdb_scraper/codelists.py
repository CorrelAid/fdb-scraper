"""Link the export's category codes to published codelists, losing nothing.

Five of the export's closed vocabularies describe the same things as codelists
published in XRepository, and one (``funding_location``) as NUTS. This module
records how, as SKOS match relations rather than as a substitution:
``funding_type`` keeps the value ``"zuschuss"``, and the fact that it is also
``001 Zuschuss`` in ``urn:xoev-de:stmd:codeliste:finanzierungsform`` is published
alongside for whoever wants it.

That way round because substituting loses data. Every codelist here is missing at
least one concept the export uses -- Garantie, Mobilität, Frauenförderung, and
the residual "Sonstige" that a controlled vocabulary correctly refuses to have --
so publishing the codelist's codes as the values would discard real categories to
look standards-compliant. Linking keeps all 38443 category values and still gives
a consumer the standard code wherever one exists.

Matches are **derived, not typed**. Both sides carry a label -- the export's from
its own label bundle via :mod:`fdb_scraper.generated.vocab`, the codelist's from
:mod:`fdb_scraper.generated.codelist_data` -- so a normalised label match pairs them. Two
independent sources agreeing on a label is evidence for a mapping; a hand-typed
code is evidence of nothing. It caught a real error: codes typed from
``xflb-baukasten.xsd`` had "Beteiligung" as 005, which is really "Bürgschaften",
because that XSD duplicates 003/004 and shifts everything after it.

What the match cannot pair is declared in :data:`MANUAL` (a relation the labels
cannot show) or :data:`NO_MATCH` (nothing in the codelist corresponds). A category
in neither raises on import, so a new upstream category or an upstream relabel
stops the build rather than passing through unnoticed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import polars as pl

from fdb_scraper.generated import CODELISTS, CLOSED_VOCABS
from fdb_scraper.schema import RENAMES

# Published column -> (codelist, key into vocab.CLOSED_VOCABS).
LINKED = {
    "funding_type": ("finanzierungsform", "foerderart"),
    "funding_body": ("geldgebende-institution", "foerdergeber"),
    "funding_location": ("nuts", "foerdergebiet"),
    "funding_area": ("foerderbereich", "foerderbereich"),
    "eligible_applicants": ("foerdernehmende", "foerderberechtigte"),
}

IDENTIFIERS = {name: cl["identifier"] for name, cl in CODELISTS.items()}
VERSIONS = {name: cl["version"] for name, cl in CODELISTS.items()}

# SKOS mapping properties, in the direction "our concept -> their concept".
EXACT = "skos:exactMatch"  # the same concept
NARROW = "skos:narrowMatch"  # theirs is narrower than ours
BROAD = "skos:broadMatch"  # theirs is broader than ours

_PUNCT_RE = re.compile(r"[^0-9a-zäöüß ]")


@dataclass(frozen=True)
class Match:
    """One codelist code an export category corresponds to."""

    code: str
    relation: str = EXACT


# Matches the label comparison cannot make: the two sides word it differently, or
# the relation is not equivalence. Every entry states why.
MANUAL = {
    "finanzierungsform": {
        # Same concept, different wording.
        "darlehen": (Match("002"),),  # "Darlehen" / "Kredit/Darlehen"
        "buergschaft": (Match("005"),),  # singular / "Bürgschaften"
    },
    "nuts": {
        # NUTS level 0, the parent of DE1..DEG. This is why NUTS is used and not
        # the XÖV Bundesland codelist, which has nothing above the 16 Länder.
        "_bundesweit": (Match("DE"),),  # "bundesweit" / "Deutschland"
    },
    "foerdernehmende": {
        # Singular / plural, same concept.
        "privatperson": (Match("001"),),  # / "Privatpersonen"
        "forschungseinrichtung": (Match("006"),),  # / "Forschungseinrichtungen"
        "kommune": (Match("009"),),  # / "Kommunen"
        "verband_vereinigung": (Match("011"),),  # / "Verbände"
        # Not equivalence: the export's category spans three codes, which is why
        # substituting it was impossible and linking is not. 1506 programmes.
        "unternehmen": (
            Match("002", NARROW),  # Kleine und mittlere Unternehmen
            Match("003", NARROW),  # Einzelunternehmen
            Match("005", NARROW),  # Großunternehmen
        ),
        # "Behörden" is narrower than an Öffentliche Einrichtung.
        "oeffentliche_einrichtung": (Match("008", NARROW),),
    },
}

# Export categories nothing in the codelist corresponds to, and why.
NO_MATCH = {
    "finanzierungsform": {
        # A Garantie is an independent undertaking; a Bürgschaft (§765 BGB) is
        # accessory to a main debt. The export states the distinction outright --
        # Hermesdeckungen and InvestEU carry both -- so 005 is not this concept.
        "garantie": "an independent undertaking, not the accessory 005 Bürgschaften",
        # A controlled vocabulary has no catch-all, correctly: an "other" code
        # makes the list unfalsifiable and any count over it meaningless.
        "sonstige": "no catch-all code, by design",
    },
    "nuts": {
        # All 10 programmes are international collaborations (Ukraine, India,
        # Southeast Asia, EUKI, IPCEI, Hermes-CIRR, ...).
        "sonstige": "means abroad; NUTS has no code for unspecified foreign",
    },
    "foerderbereich": {
        # Both are plausible additions -- the list went 1 -> 1.1 to add 022
        # Integration -- so worth asking the Herausgeber (StMD) for.
        "mobilitaet": "no counterpart in the codelist",
        "frauenfoerderung": "no counterpart in the codelist",
    },
    "foerdernehmende": {
        "existenzgruenderin": "no counterpart in the codelist",
        "bildungseinrichtung": "no counterpart in the codelist",
        "hochschule": "no counterpart in the codelist",
    },
}


def _norm(label: str | None) -> str | None:
    """Reduce a label to what both sides spell the same way.

    The export writes "Aus- & Weiterbildung" where genericode writes "Aus und
    Weiterbildung", so "&" becomes "und" and punctuation goes. Deliberately not
    fuzzy: a near-match is a mapping nobody checked.
    """
    if label is None:
        return None
    label = label.lower().replace("&", " und ").replace("/", " ").replace("-", " ")
    return " ".join(_PUNCT_RE.sub(" ", label).split()) or None


def _build(codelist: str, vocab_key: str) -> dict[str, tuple[Match, ...]]:
    """Match every export category, or state why it cannot be matched."""
    codes = CODELISTS[codelist]["codes"]
    by_label: dict[str | None, str] = {}
    for code, label in codes.items():
        by_label.setdefault(_norm(label), code)

    manual, without = MANUAL.get(codelist, {}), NO_MATCH.get(codelist, {})
    declared = [m.code for ms in manual.values() for m in ms]
    if unknown := [c for c in declared if c not in codes]:
        raise RuntimeError(f"{codelist}: manual codes not in the codelist: {unknown}")

    out: dict[str, tuple[Match, ...]] = {}
    for slug, label in CLOSED_VOCABS[vocab_key].items():
        if slug in manual:
            out[slug] = manual[slug]
        elif (code := by_label.get(_norm(label))) is not None:
            out[slug] = (Match(code),)
        elif slug in without:
            out[slug] = ()
        else:
            raise RuntimeError(
                f"{codelist}: {slug!r} ({label!r}) matches no code in "
                f"{CODELISTS[codelist]['identifier']} {CODELISTS[codelist]['version']}. "
                "Add it to MANUAL with the relation, or to NO_MATCH with the reason."
            )
    return out


MATCHES: dict[str, dict[str, tuple[Match, ...]]] = {
    codelist: _build(codelist, vocab_key) for codelist, vocab_key in LINKED.values()
}

# Codelist code -> its URI, where the codelist publishes per-code URIs. XÖV
# identifies its lists by URN and gives codes no URI of their own, so only NUTS
# has one; for the rest a consumer resolves the code against the list identifier.
CODE_URI = {"nuts": "http://data.europa.eu/nuts/code/{}"}


def code_uri(codelist: str, code: str) -> str | None:
    """Dereferenceable URI for a code, or None if the codelist publishes none."""
    template = CODE_URI.get(codelist)
    return template.format(code) if template else None


def matches() -> pl.DataFrame:
    """Every export category with the codelist codes it corresponds to.

    One row per (column, export code, match); a category with no match gets one
    row with the match columns null, so the table is a complete statement about
    every category rather than only the mapped ones.
    """
    rows = []
    for column, (codelist, vocab_key) in LINKED.items():
        cl = CODELISTS[codelist]
        for code, ms in MATCHES[codelist].items():
            common = {
                "column": column,
                "code": code,
                "label": CLOSED_VOCABS[vocab_key][code],
                "codelist": cl["identifier"],
                "codelist_version": cl["version"],
            }
            if not ms:
                rows.append(
                    {
                        **common,
                        "relation": None,
                        "codelist_code": None,
                        "codelist_label": None,
                        "codelist_uri": None,
                        "reason": NO_MATCH[codelist][code],
                    }
                )
                continue
            rows += [
                {
                    **common,
                    "relation": m.relation,
                    "codelist_code": m.code,
                    "codelist_label": cl["codes"][m.code],
                    "codelist_uri": code_uri(codelist, m.code),
                    "reason": None,
                }
                for m in ms
            ]
    return pl.DataFrame(
        rows,
        schema={
            "column": pl.String,
            "code": pl.String,
            "label": pl.String,
            "codelist": pl.String,
            "codelist_version": pl.String,
            "relation": pl.String,
            "codelist_code": pl.String,
            "codelist_label": pl.String,
            "codelist_uri": pl.String,
            "reason": pl.String,
        },
    ).sort("column", "code", "codelist_code", nulls_last=True)


def unmatched(df: pl.DataFrame | None = None) -> pl.DataFrame:
    """Export categories no codelist code corresponds to, and why.

    Not a loss report -- nothing is dropped -- but the measure of how far the
    alignment reaches. Pass the published table to get programme counts.
    """
    rows = []
    for column, (codelist, vocab_key) in LINKED.items():
        for code, reason in NO_MATCH.get(codelist, {}).items():
            n = None
            if df is not None and column in df.columns:
                n = df.filter(pl.col(column).list.contains(code)).height
            rows.append(
                {
                    "column": column,
                    "codelist": CODELISTS[codelist]["identifier"],
                    "code": code,
                    "label": CLOSED_VOCABS[vocab_key][code],
                    "reason": reason,
                    "programmes": n,
                }
            )
    return pl.DataFrame(
        rows,
        schema={
            "column": pl.String,
            "codelist": pl.String,
            "code": pl.String,
            "label": pl.String,
            "reason": pl.String,
            "programmes": pl.UInt32,
        },
    ).sort("programmes", descending=True, nulls_last=True)


_stale = sorted(set(LINKED) - set(RENAMES.values()) - set(CLOSED_VOCABS))
if _stale:  # pragma: no cover -- guards a rename in publish.RENAMES
    raise RuntimeError(f"linked columns that are not published: {_stale}")
