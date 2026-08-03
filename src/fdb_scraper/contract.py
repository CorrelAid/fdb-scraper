"""Check that the export still has the shape the parser assumes.

The parser reads properties by name and expects a particular container for each
one -- ``gsb:summary`` is RichText, so its text lives in a ``<text>`` child. If
upstream changed it to a String, ``_rich_text`` would return ``None`` for every
programme and nothing downstream would notice: the column is nullable, so the
pandera schema passes and 1975 filled values silently become empty.

That class of drift is invisible to value-level validation, so it is checked
structurally instead:

* the root element and its attributes,
* the document type, which is *not* implied by the directory -- ``Kontakt/``
  holds a few ExternalLink and Address documents,
* every property name, against the set the parser knows for that type,
* every property's declared type, against the recorded one,
* the child element each property type must carry.

An unknown property name raises rather than warns: a new field is exactly what
would otherwise be dropped without a trace. Regenerate the contract with
``scripts/gen_contract.py`` and review the diff.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree as ET

from fdb_scraper.generated import DOCUMENTS, PROPERTY_CHILD
from fdb_scraper.links import CONTENT_DIR
from fdb_scraper.parser import parse_xml

ROOT_TAG = "document"
ROOT_ATTRS = frozenset({"name", "path", "type"})
# Documents are reported per violation, with this many example paths each.
EXAMPLES = 3


class ContractError(RuntimeError):
    """The export no longer matches the structure the parser assumes."""


def violations(root: ET.Element) -> Iterator[str]:
    """Yield a message for every structural assumption the document breaks."""
    if root.tag != ROOT_TAG:
        yield f"root element is <{root.tag}>, expected <{ROOT_TAG}>"
        return
    if missing := ROOT_ATTRS - set(root.attrib):
        yield f"root is missing attributes {sorted(missing)}"

    doc_type = root.get("type")
    known = DOCUMENTS.get(doc_type)
    if known is None:
        yield f"unknown document type {doc_type!r}"
        return

    for prop in root.findall("property"):
        name, declared = prop.get("name"), prop.get("type")
        expected = known.get(name)
        if expected is None:
            yield f"{doc_type}: unknown property {name!r} of type {declared!r}"
            continue
        if declared != expected:
            yield f"{doc_type}: property {name!r} is {declared!r}, expected {expected!r}"
            continue
        child = PROPERTY_CHILD.get(declared)
        tags = {c.tag for c in prop}
        # Exactly the expected child: every property in the export has one, and a
        # property with none would read as null rather than as a violation.
        if child is not None and tags != {child}:
            found = f"children {sorted(tags)}" if tags else "no children"
            yield f"{declared} property {name!r} has {found}, expected exactly <{child}>"


def check_export(export_root: str | Path) -> int:
    """Validate every document in the export. Returns the number checked."""
    content = Path(export_root) / CONTENT_DIR
    if not content.is_dir():
        raise FileNotFoundError(content)

    found: dict[str, list[str]] = {}
    counts: Counter[str] = Counter()
    checked = 0
    for path in content.rglob("*.xml"):
        checked += 1
        try:
            document = parse_xml(path)
        except ET.ParseError:
            # Report it like any other violation: a corrupt document should land
            # in the summary rather than abort the run with a bare ParseError.
            # The message stays constant so several corrupt documents group.
            messages: list[str] = ["document is not parseable as XML"]
        else:
            messages = list(violations(document))
        for message in messages:
            counts[message] += 1
            examples = found.setdefault(message, [])
            if len(examples) < EXAMPLES:
                examples.append(str(path.relative_to(content)))

    if counts:
        report = "\n".join(
            f"  {message} ({count} documents, e.g. {', '.join(found[message])})"
            for message, count in counts.most_common()
        )
        raise ContractError(
            f"export structure changed in {checked} documents:\n{report}\n"
            "If the change is expected, regenerate with scripts/gen_contract.py."
        )
    return checked
