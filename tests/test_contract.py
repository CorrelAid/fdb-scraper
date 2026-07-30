import shutil
from xml.etree import ElementTree as ET

import pytest

from fdb_scraper import collect
from fdb_scraper.contract import ContractError, check_export, violations
from fdb_scraper.generated import DOCUMENTS, PROPERTY_CHILD
from fdb_scraper.links import CONTENT_DIR
from fdb_scraper.scraper import parse_xml
from tests.conftest import PROGRAMME, property_named as _property


def _programme() -> ET.Element:
    return parse_xml(PROGRAMME)


def test_the_fixture_satisfies_the_contract(export_dir):
    assert check_export(export_dir) == 32


def test_an_unchanged_document_reports_nothing():
    assert list(violations(_programme())) == []


def test_a_retyped_property_is_caught():
    """The drift value-level validation cannot see: RichText -> String would make
    _rich_text return None for every programme, and the column is nullable."""
    root = _programme()
    _property(root, "gsb:summary").set("type", "String")
    assert any("gsb:summary" in v and "expected 'RichText'" in v for v in violations(root))


def test_a_new_property_is_caught():
    """Otherwise the parser ignores it and the field is dropped without a trace."""
    root = _programme()
    ET.SubElement(root, "property", {"name": "gsb:brandNew", "type": "String"})
    assert any("unknown property 'gsb:brandNew'" in v for v in violations(root))


def test_a_renamed_property_is_caught():
    root = _programme()
    _property(root, "gsb:teaserText").set("name", "gsb:teaser")
    assert any("unknown property 'gsb:teaser'" in v for v in violations(root))


def test_a_changed_container_element_is_caught():
    root = _programme()
    prop = _property(root, "gsb:summary")
    prop.remove(prop.find("text"))
    ET.SubElement(prop, "value")
    assert any("expected exactly <text>" in v for v in violations(root))


def test_an_unknown_document_type_is_caught():
    root = _programme()
    root.set("type", "gsb:SomethingElse")
    assert any("unknown document type" in v for v in violations(root))


def test_a_missing_root_attribute_is_caught():
    root = _programme()
    del root.attrib["path"]
    assert any("missing attributes ['path']" in v for v in violations(root))


def test_an_unexpected_root_element_is_caught():
    assert list(violations(ET.Element("something"))) == [
        "root element is <something>, expected <document>"
    ]


def test_check_export_aggregates_and_names_examples(tmp_path, export_dir):
    broken = tmp_path / "export"
    shutil.copytree(export_dir, broken)
    for path in (broken / CONTENT_DIR / "Foerderprogramm").rglob("*.xml"):
        path.write_bytes(path.read_bytes().replace(b'"gsb:summary" type="RichText"', b'"gsb:summary" type="String"'))

    with pytest.raises(ContractError) as excinfo:
        check_export(broken)
    message = str(excinfo.value)
    assert "gsb:summary" in message
    assert "3 documents" in message
    assert "eingliederungszuschuss-bund.xml" in message
    assert "gen_contract.py" in message


def test_collect_checks_the_contract_by_default(tmp_path, export_dir):
    broken = tmp_path / "export"
    shutil.copytree(export_dir, broken)
    path = next((broken / CONTENT_DIR / "Foerderprogramm").rglob("*.xml"))
    path.write_bytes(path.read_bytes().replace(b'name="gsb:title"', b'name="gsb:heading"'))

    with pytest.raises(ContractError):
        collect(export_dir=broken)
    # ...and can be skipped when reading an export whose drift is already known.
    assert collect(export_dir=broken, check_contract=False).height == 3


def test_every_container_maps_to_one_child_element():
    for properties in DOCUMENTS.values():
        for container in properties.values():
            assert container in PROPERTY_CHILD


def test_missing_content_directory_names_the_path(tmp_path):
    with pytest.raises(FileNotFoundError):
        check_export(tmp_path)


def test_a_property_with_no_child_element_is_caught():
    """The silent case: a RichText property without <text> reads as null.

    Every property in the export has exactly one child, so requiring it costs
    nothing and closes the gap a "no unexpected children" check would leave.
    """
    root = _programme()
    prop = _property(root, "gsb:summary")
    prop.remove(prop.find("text"))
    assert any("has no children, expected exactly <text>" in v for v in violations(root))


def test_an_extra_child_element_is_caught():
    root = _programme()
    ET.SubElement(_property(root, "gsb:summary"), "value")
    assert any("expected exactly <text>" in v for v in violations(root))


def test_an_unparseable_document_is_reported_not_raised(tmp_path, export_dir):
    """A corrupt document belongs in the summary, not as a bare ParseError."""
    broken = tmp_path / "export"
    shutil.copytree(export_dir, broken)
    path = next((broken / CONTENT_DIR / "Foerderprogramm").rglob("*.xml"))
    path.write_bytes(b'<document name="x" path="/y" type="gsb:ServiceOffer"><property')

    with pytest.raises(ContractError) as excinfo:
        check_export(broken)
    message = str(excinfo.value)
    assert "not parseable as XML" in message
    assert path.name in message


def test_the_control_character_document_is_not_reported_as_unparseable(export_dir):
    """parse_xml repairs it, so it must not show up as corrupt."""
    check_export(export_dir)
