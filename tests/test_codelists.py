import polars as pl
import pytest

from fdb_scraper.generated import CODELISTS
from fdb_scraper.codelists import (
    BROAD,
    EXACT,
    IDENTIFIERS,
    LINKED,
    MANUAL,
    MATCHES,
    NARROW,
    NO_MATCH,
    VERSIONS,
    Match,
    _build,
    _norm,
    code_uri,
    matches,
    unmatched,
)
from fdb_scraper.schema import PUBLISHED_FIELDS
from fdb_scraper.generated import CLOSED_VOCABS

RELATIONS = {EXACT, NARROW, BROAD}


# -- the codelists themselves --------------------------------------------------


def test_every_codelist_has_an_identifier_a_version_and_codes():
    for name, cl in CODELISTS.items():
        assert cl["identifier"] and IDENTIFIERS[name] == cl["identifier"]
        assert cl["version"] and VERSIONS[name] == cl["version"]
        assert cl["codes"], name


def test_codelist_labels_are_all_present():
    """A code with no label cannot be matched or shown."""
    for name, cl in CODELISTS.items():
        assert all(cl["codes"].values()), name


def test_identifiers_are_urns_or_urls():
    for name, identifier in IDENTIFIERS.items():
        assert identifier.startswith(("urn:xoev-de:", "http://", "https://")), name


# -- nothing is lost ------------------------------------------------------------


def test_linking_does_not_touch_the_data(df):
    """The published values stay the export's own codes -- that is the point."""
    for column, (_, vocab_key) in LINKED.items():
        values = {v for row in df[column].to_list() for v in (row or [])}
        assert values <= set(CLOSED_VOCABS[vocab_key]), column


def test_every_linked_column_is_published():
    assert set(LINKED) <= set(PUBLISHED_FIELDS)


# -- the derivation is the check ------------------------------------------------


def test_every_matched_code_exists_in_its_codelist():
    for codelist, mapping in MATCHES.items():
        codes = set(CODELISTS[codelist]["codes"])
        for slug, ms in mapping.items():
            assert {m.code for m in ms} <= codes, (codelist, slug)


def test_every_relation_is_a_known_skos_property():
    for mapping in MATCHES.values():
        for ms in mapping.values():
            assert {m.relation for m in ms} <= RELATIONS


def test_every_export_category_is_decided():
    for codelist, vocab_key in LINKED.values():
        assert set(MATCHES[codelist]) == set(CLOSED_VOCABS[vocab_key]), codelist


def test_a_new_upstream_category_raises():
    """The point of deriving: an unknown category cannot pass silently."""
    vocab = dict(CLOSED_VOCABS["foerdergeber"], neue_kategorie="Neue Kategorie")
    with pytest.MonkeyPatch.context() as m:
        m.setitem(CLOSED_VOCABS, "foerdergeber", vocab)
        with pytest.raises(RuntimeError, match="neue_kategorie"):
            _build("geldgebende-institution", "foerdergeber")


def test_a_relabelled_category_raises_rather_than_rematching():
    """If the export renames a category, the match must stop, not guess."""
    vocab = dict(CLOSED_VOCABS["foerdergeber"], bund="Bundesrepublik")
    with pytest.MonkeyPatch.context() as m:
        m.setitem(CLOSED_VOCABS, "foerdergeber", vocab)
        with pytest.raises(RuntimeError, match="bund"):
            _build("geldgebende-institution", "foerdergeber")


def test_a_manual_code_that_left_the_codelist_raises():
    with pytest.MonkeyPatch.context() as m:
        m.setitem(MANUAL, "nuts", {"_bundesweit": (Match("ZZ"),)})
        with pytest.raises(RuntimeError, match="not in the codelist"):
            _build("nuts", "foerdergebiet")


def test_derived_matches_really_agree_on_the_label():
    """Every match not in MANUAL was made because the labels are the same."""
    for codelist, vocab_key in LINKED.values():
        manual = MANUAL.get(codelist, {})
        codes = CODELISTS[codelist]["codes"]
        for slug, ms in MATCHES[codelist].items():
            if not ms or slug in manual:
                continue
            assert len(ms) == 1 and ms[0].relation == EXACT
            label = CLOSED_VOCABS[vocab_key][slug]
            assert _norm(label) == _norm(codes[ms[0].code]), slug


def test_manual_and_unmatched_do_not_overlap():
    for codelist in MATCHES:
        assert not set(MANUAL.get(codelist, {})) & set(NO_MATCH.get(codelist, {}))


# -- the relations that make this lossless -------------------------------------


def test_a_broader_export_category_gets_narrow_matches():
    """'unternehmen' spans three codes, which is why substituting was impossible."""
    ms = MATCHES["foerdernehmende"]["unternehmen"]
    assert {m.code for m in ms} == {"002", "003", "005"}
    assert {m.relation for m in ms} == {NARROW}


def test_oeffentliche_einrichtung_is_broader_than_behoerden():
    ms = MATCHES["foerdernehmende"]["oeffentliche_einrichtung"]
    assert ms == (Match("008", NARROW),)
    assert CODELISTS["foerdernehmende"]["codes"]["008"] == "Behörden"


def test_nationwide_scope_matches_the_nuts_country_code():
    """Why NUTS and not the XÖV Bundesland list, which stops at the 16 Länder."""
    assert MATCHES["nuts"]["_bundesweit"] == (Match("DE"),)
    assert CODELISTS["nuts"]["codes"]["DE"] == "Deutschland"


def test_the_schleswig_holstein_typo_is_fixed_by_the_match():
    """The export misspells the slug; the label still matches, so DEF is derived."""
    assert MATCHES["nuts"]["schlesig_holstein"] == (Match("DEF"),)


# -- what has no counterpart, and why ------------------------------------------


def test_garantie_is_not_matched_to_buergschaften():
    """§765 BGB: a Bürgschaft is accessory to a main debt, a Garantie is not."""
    assert MATCHES["finanzierungsform"]["garantie"] == ()
    assert "accessory" in NO_MATCH["finanzierungsform"]["garantie"]


def test_the_residual_categories_have_no_match():
    assert MATCHES["finanzierungsform"]["sonstige"] == ()
    assert MATCHES["nuts"]["sonstige"] == ()


def test_unmatched_states_a_reason_for_every_entry():
    u = unmatched()
    assert u.columns == [
        "column", "codelist", "code", "label", "reason", "programmes",
    ]
    assert u["reason"].null_count() == 0
    assert u.height == sum(len(v) for v in NO_MATCH.values())


def test_unmatched_counts_programmes_when_given_the_table(df):
    assert unmatched(df)["programmes"].null_count() == 0


# -- the published match table -------------------------------------------------


def test_matches_covers_every_category():
    m = matches()
    assert m["code"].null_count() == 0
    for column, (codelist, _) in LINKED.items():
        got = set(m.filter(pl.col("column") == column)["code"])
        assert got == set(MATCHES[codelist]), column


def test_a_matched_row_carries_both_labels_and_no_reason():
    m = matches().filter(pl.col("codelist_code").is_not_null())
    assert m["codelist_label"].null_count() == 0
    assert m["relation"].null_count() == 0
    assert m["reason"].null_count() == m.height


def test_an_unmatched_row_carries_a_reason_and_no_code():
    m = matches().filter(pl.col("codelist_code").is_null())
    assert m["reason"].null_count() == 0
    assert m["relation"].null_count() == m.height
    assert m.height == sum(len(v) for v in NO_MATCH.values())


def test_only_nuts_publishes_per_code_uris():
    """XÖV identifies lists by URN and gives codes no URI of their own."""
    assert code_uri("nuts", "DE1") == "http://data.europa.eu/nuts/code/DE1"
    assert code_uri("finanzierungsform", "001") is None
    uris = matches().filter(pl.col("codelist_uri").is_not_null())
    assert set(uris["column"]) == {"funding_location"}


# -- the label normalisation ---------------------------------------------------


def test_norm_folds_the_two_spellings_of_und():
    assert _norm("Aus- & Weiterbildung") == _norm("Aus und Weiterbildung")


def test_norm_is_not_fuzzy():
    """A near-match is a mapping nobody checked, so it must not match."""
    assert _norm("Bürgschaft") != _norm("Bürgschaften")
    assert _norm("Darlehen") != _norm("Kredit/Darlehen")
