import pytest

from fdb_scraper.links import CONTACT_KEYS, page_url, resolve

ARBEITGEBERSERVICE = "/BMWI/FDB/Content/DE/Kontakt/B/bundesagentur-fuer-arbeit-arbeitgeberservice"
BA = "/BMWI/FDB/Content/DE/Kontakt/B/bundesagentur-fuer-arbeit"
FFA_LINK = "/BMWI/FDB/Content/DE/ExternerLink/F/ffa-de"


def test_contact_carries_its_own_fields(docs):
    contact = docs[ARBEITGEBERSERVICE]
    assert contact["institution"] == "Bundesagentur für Arbeit (BA)"
    assert contact["phone"] == "+49 800 455-5520"


def test_address_is_merged_from_the_linked_adresse_document(docs):
    contact = docs[ARBEITGEBERSERVICE]
    assert contact["road"] == "Regensburger Straße"
    assert contact["house_id"] == "104"
    assert contact["zip_code"] == "90478"
    assert contact["city"] == "Nürnberg"


def test_website_is_resolved_through_the_externerlink_document(docs):
    """Kontakt.gsb:website is a link to a document, not a URL."""
    assert docs[ARBEITGEBERSERVICE]["website"] == "http://www.arbeitsagentur.de/"


def test_external_link_documents_expose_their_target(docs):
    assert docs[FFA_LINK]["url"] == "https://www.ffa.de/"


def test_documents_without_a_url_fall_back_to_their_page_on_the_site(docs):
    """Kontakt, Archiv and Foerdergeber documents have no gsb:url of their own."""
    assert (
        docs[BA]["url"]
        == "https://www.foerderdatenbank.de/FDB/Content/DE/Kontakt/B/bundesagentur-fuer-arbeit.html"
    )


def test_page_url_mirrors_the_export_tree_below_bmwi():
    assert page_url("/BMWI/FDB/Content/DE/Archiv/x") == (
        "https://www.foerderdatenbank.de/FDB/Content/DE/Archiv/x.html"
    )


def test_every_indexed_document_has_all_contact_keys(docs):
    """Missing keys would silently become nulls in a different column."""
    for doc in docs.values():
        assert set(CONTACT_KEYS) <= set(doc)


def test_links_from_several_trees_are_indexed_together(docs):
    """externer_link may target Kontakt or Archiv, kontakt may target Foerdergeber."""
    trees = {key.split("/DE/")[1].split("/")[0] for key in docs}
    assert {"ExternerLink", "Kontakt", "Adresse", "Foerdergeber"} <= trees


def test_missing_content_directory_names_the_path(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve(tmp_path)
