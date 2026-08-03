import hashlib

import polars as pl

from fdb_scraper.process import _id_url, add_keyword_segments, decode, process
from fdb_scraper.config import CODE_ALIASES, CONSUMED_FIELDS, DROPPED_FIELDS, RENAMES
from fdb_scraper.schema import USEABLE_FIELDS
from fdb_scraper.parser import ALL_FIELDS
from fdb_scraper.scraper import scrape
from tests.conftest import EINGLIEDERUNG, EINSTIEGSGELD, EXPORT, FILM


def test_classifier_links_reduce_to_leaf_labels():
    df = pl.DataFrame(
        {"foerderart": [["target:/BMWI/SiteGlobals/Categories/FDB/Foerderart/zuschuss"]]}
    )
    assert decode(df)["foerderart"].to_list() == [["zuschuss"]]


def test_duplicate_nationwide_categories_collapse_to_one():
    """_bundesweit and bundesweit are byte-identical category records upstream."""
    base = "target:/BMWI/SiteGlobals/Categories/FDB/Foerdergebiet/"
    df = pl.DataFrame({"foerdergebiet": [[f"{base}bundesweit"], [f"{base}_bundesweit"]]})
    assert decode(df)["foerdergebiet"].to_list() == [["_bundesweit"], ["_bundesweit"]]


def test_a_programme_carrying_both_twins_ends_up_with_one_entry():
    base = "target:/BMWI/SiteGlobals/Categories/FDB/Foerdergebiet/"
    df = pl.DataFrame({"foerdergebiet": [[f"{base}bundesweit", f"{base}_bundesweit"]]})
    assert decode(df)["foerdergebiet"].to_list() == [["_bundesweit"]]


def test_categories_repeated_within_one_programme_are_deduplicated():
    """Two programmes list _bundesweit twice; hrefs accumulate across blocks."""
    base = "target:/BMWI/SiteGlobals/Categories/FDB/Foerdergebiet/"
    df = pl.DataFrame({"foerdergebiet": [[f"{base}_bundesweit", f"{base}_bundesweit"]]})
    assert decode(df)["foerdergebiet"].to_list() == [["_bundesweit"]]


def test_other_upstream_oddities_are_left_verbatim():
    """Only the documented alias is normalised; the typo code is not."""
    base = "target:/BMWI/SiteGlobals/Categories/FDB/Foerdergebiet/"
    df = pl.DataFrame({"foerdergebiet": [[f"{base}schlesig_holstein"]]})
    assert decode(df)["foerdergebiet"].to_list() == [["schlesig_holstein"]]
    assert set(CODE_ALIASES) == {"foerdergebiet"}


def test_lists_of_only_nulls_are_left_alone():
    """foerdertermin is List(Null); str.extract on it would raise."""
    df = pl.DataFrame({"foerdertermin": [[None]]}, schema={"foerdertermin": pl.List(pl.Null)})
    assert decode(df)["foerdertermin"].to_list() == [[None]]


def test_useable_fields_are_all_fields_minus_the_dead_ones():
    assert set(USEABLE_FIELDS) == set(ALL_FIELDS) - DROPPED_FIELDS
    assert DROPPED_FIELDS < set(ALL_FIELDS)
    for dead in ("date_of_expiration", "unternehmensalter", "languages", "path"):
        assert dead not in USEABLE_FIELDS


def test_id_url_is_the_slug_below_foerderprogramm():
    url = (
        "https://www.foerderdatenbank.de/FDB/Content/DE/Foerderprogramm/"
        "Land/Baden-Wuerttemberg/Erfahrungsaustauschgruppen-im-Handwerk.html"
    )
    assert _id_url(url) == "land-baden-wuerttemberg-erfahrungsaustauschgruppen-im-handwerk"


def test_id_hash_is_the_md5_of_id_url(row):
    r = row(EINGLIEDERUNG)
    assert r["id_hash"] == hashlib.md5(r["id_url"].encode()).hexdigest()


def test_published_names_replace_the_misleading_export_names(df):
    """gsb:regulatoryFWork holds eligibility, gsb:progress holds processing time."""
    for export_name, published in RENAMES.items():
        assert published in df.columns
        assert export_name not in df.columns


def test_zusatzinfos_stay_split_into_their_sub_sections(df):
    for column in ("procedure", "deadlines", "processing_time", "required_documents",
                   "legal_requirements"):
        assert column in df.columns
    # The old blob that concatenated all five is gone.
    assert "more_info" not in df.columns


def test_dropped_fields_are_absent(df):
    for dead in CONSUMED_FIELDS:
        assert dead not in df.columns


def test_contact_columns_are_flat_and_describe_the_first_contact(row):
    r = row(EINGLIEDERUNG)
    assert r["contact_info_institution"] == "Bundesagentur für Arbeit (BA)"
    assert r["contact_info_road"] == "Regensburger Straße"
    assert r["contact_info_city"] == "Nürnberg"
    assert r["contact_info_website"] == "http://www.arbeitsagentur.de/"


def test_extra_contacts_stay_recoverable_through_contact_ids(row):
    """33 programmes link two contacts; the flat columns only describe one."""
    r = row(EINSTIEGSGELD)
    assert len(r["contact_ids"]) == 2
    assert r["contact_info_institution"] is not None


def test_a_programme_without_address_data_gets_nulls_not_an_error(row):
    r = row(EINSTIEGSGELD)
    assert r["contact_info_institution"] is not None
    assert r["contact_info_road"] is None


def test_further_links_resolve_to_url_and_title(row):
    links = row(EINGLIEDERUNG)["further_links"]
    assert {"url", "title"} == set(links[0])
    assert links[0]["url"] == (
        "https://www.arbeitsagentur.de/unternehmen/finanziell/foerderung-arbeitsaufnahme"
    )
    assert all(link["url"].startswith("http") for link in links)


def test_every_further_link_resolves(df):
    """A null url here once passed validation vacuously; see test_schema.py."""
    unresolved = df.select(
        pl.col("further_links").list.eval(pl.element().struct.field("url").is_null()).list.any()
    ).to_series()
    assert not unresolved.any()


def test_category_columns_carry_codes(row):
    assert row(EINGLIEDERUNG)["funding_type"] == ["zuschuss"]


def test_the_nationwide_alias_is_applied_end_to_end(row):
    """This programme's raw value is the duplicate 'bundesweit' category."""
    assert row(FILM)["funding_location"] == ["_bundesweit"]


# --- the inferred column ------------------------------------------------------


def _frame(keywords: list[str | None]) -> pl.DataFrame:
    return pl.DataFrame({"keywords": keywords}, schema={"keywords": pl.String})


def test_keyword_segments_are_looked_up_by_the_raw_string():
    """A dict lookup, exactly as add_links looks up documents.

    Keyed on the string and not on a programme, because the segmentation is a
    function of the string: two programmes with the same keywords share one row in
    keyword_segments and one model call.
    """
    df = add_keyword_segments(
        _frame(["Erneuerbare Energien Zuschuss", "Erneuerbare Energien Zuschuss"]),
        {"Erneuerbare Energien Zuschuss": ["Erneuerbare Energien", "Zuschuss"]},
    )
    assert df["keywords_extracted"].to_list() == [
        ["Erneuerbare Energien", "Zuschuss"],
        ["Erneuerbare Energien", "Zuschuss"],
    ]
    assert df.schema["keywords_extracted"] == pl.List(pl.String)


def test_an_unsegmented_value_publishes_as_null_not_as_a_guess():
    """The state after an export brings a keywords string the tagger has not seen."""
    df = add_keyword_segments(_frame(["Etwas ganz Neues", None]), {})
    assert df["keywords_extracted"].to_list() == [None, None]


def test_the_segmentation_is_optional():
    """collect() cannot supply it: nothing in one export says how to split the string."""
    assert "keywords_extracted" not in process(
        scrape(USEABLE_FIELDS, export_dir=EXPORT)
    ).columns
