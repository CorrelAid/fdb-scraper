"""Regenerate ``fdb_scraper/generated/codelist_data.py`` from the publishing registries.

Four codelists come from XRepository as genericode, one (NUTS) from Eurostat.
Fetching them beats reading codes off ``xflb-baukasten.xsd``, whose inline
enumerations are out of step with the published lists: its ``finanzierungsform``
repeats "Anteilsfinanzierung/Kapitalbeteiligung" at 003 and 004, which shifts
every later code by one and yields seven entries where the codelist has six.

    uv run python scripts/gen_codelist_data.py            # regenerate
    uv run python scripts/gen_codelist_data.py --check    # newer versions?

Versions are pinned, not followed: a code's identity includes its version, and a
new version may renumber or re-label. Review the diff -- a changed code changes
what the dataset says.
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

XREPOSITORY = "https://www.xrepository.de/api"
# Eurostat's NUTS distribution. NUTS is used for funding_location because the
# XÖV Bundesland codelist has 16 codes and no way to say "nationwide", which is
# 659 of 2500 programmes; NUTS has DE above DE1..DEG.
NUTS_CSV = "https://gisco-services.ec.europa.eu/distribution/v2/nuts/csv/NUTS_AT_{v}.csv"
OUT = Path(__file__).parent.parent / "src" / "fdb_scraper" / "generated" / "codelist_data.py"

# XÖV codelists: name -> (URN, pinned version).
XOEV = {
    "finanzierungsform": ("urn:xoev-de:stmd:codeliste:finanzierungsform", "1"),
    "geldgebende-institution": (
        "urn:xoev-de:stmd:codeliste:geldgebende-institution",
        "1",
    ),
    "foerderbereich": ("urn:xoev-de:stmd:codeliste:foerderbereich", "1.1"),
    "foerdernehmende": ("urn:xoev-de:stmd:codeliste:foerdernehmende", "1"),
}

NUTS_VERSION = "2024"
# Germany and its Bundesländer: NUTS level 0 and 1, so codes are at most 3 long.
NUTS_PREFIX = "DE"


def published_versions(urn: str) -> list[str]:
    """Every version XRepository lists for a codelist, oldest first."""
    root = ET.fromstring(
        requests.get(f"{XREPOSITORY}/xrepository/{urn}", timeout=60).content
    )
    prefix = f"{urn}_"
    return [
        el.text.removeprefix(prefix)
        for el in root.iter()
        if el.tag.endswith("versionCodeliste.kennung") and el.text
    ]


def xoev_codes(urn: str, version: str) -> dict[str, str | None]:
    """``code -> German label`` for one codelist version, from its genericode."""
    url = f"{XREPOSITORY}/version_codeliste/{urn}_{version}/genericode"
    root = ET.fromstring(requests.get(url, timeout=60).content)
    # Row and Value are unprefixed: the genericode default namespace is not
    # declared, so they carry no namespace at all while gc:CodeList does.
    out: dict[str, str | None] = {}
    for row in root.iter("Row"):
        cells = {v.get("ColumnRef"): v.findtext("SimpleValue") for v in row.findall("Value")}
        code = next(v for k, v in cells.items() if k.lower() == "code")
        out[code] = next((v for k, v in cells.items() if k.lower() != "code"), None)
    return out


def nuts_codes(version: str) -> dict[str, str]:
    """German NUTS level 0 and 1 codes, ordered DE then DE1..DEG."""
    text = requests.get(NUTS_CSV.format(v=version), timeout=120).content.decode("utf-8-sig")
    rows = [
        r
        for r in csv.DictReader(io.StringIO(text))
        if r["NUTS_ID"].startswith(NUTS_PREFIX) and len(r["NUTS_ID"]) <= 3
    ]
    if not rows:
        raise SystemExit(f"NUTS {version}: no {NUTS_PREFIX} codes found")
    return {
        r["NUTS_ID"]: r["NAME_LATN"]
        for r in sorted(rows, key=lambda r: (len(r["NUTS_ID"]), r["NUTS_ID"]))
    }


def check() -> int:
    """Report pinned versions against what the registries publish now.

    Exits non-zero if a newer version exists, so CI flags it without the dataset
    changing under anyone.
    """
    veraltet = 0
    for name, (urn, version) in XOEV.items():
        newer = [v for v in published_versions(urn) if v > version]
        veraltet += bool(newer)
        print(f"{name:26} pinned {version:6} {'newer: ' + ', '.join(newer) if newer else 'current'}")
    # Eurostat publishes no version index, so probe the next likely release.
    naechste = str(int(NUTS_VERSION) + 3)
    vorhanden = requests.head(NUTS_CSV.format(v=naechste), timeout=60).ok
    veraltet += vorhanden
    print(f"{'nuts':26} pinned {NUTS_VERSION:6} {'newer: ' + naechste if vorhanden else 'current'}")
    return 1 if veraltet else 0


def main() -> None:
    tables: dict[str, dict] = {}
    for name, (urn, version) in XOEV.items():
        available = published_versions(urn)
        if version not in available:
            raise SystemExit(f"{urn}: version {version} not published, have {available}")
        newer = [v for v in available if v > version]
        tables[name] = {
            "identifier": urn,
            "version": version,
            "codes": xoev_codes(urn, version),
            "newer": newer,
        }
    tables["nuts"] = {
        "identifier": "http://data.europa.eu/nuts",
        "version": NUTS_VERSION,
        "codes": nuts_codes(NUTS_VERSION),
        "newer": [],
    }

    lines = [
        '"""Published codelists, fetched from their registries and committed.',
        "",
        "Generated by ``scripts/gen_codelist_data.py``; do not edit by hand. One",
        "entry per pinned codelist version: its identifier, the version, and",
        "``code -> label`` exactly as the registry states it.",
        "",
        "``codelists.py`` matches the export's own categories against these labels",
        "and replaces the export's codes with the published ones.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "CODELISTS: dict[str, dict] = {",
    ]
    for name, t in tables.items():
        note = f"  # newer published: {', '.join(t['newer'])}" if t["newer"] else ""
        lines += [
            f"    {name!r}: {{{note}",
            f"        'identifier': {t['identifier']!r},",
            f"        'version': {t['version']!r},",
            "        'codes': {",
            *(f"            {c!r}: {label!r}," for c, label in t["codes"].items()),
            "        },",
            "    },",
        ]
    lines += ["}", ""]
    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT} -- {len(tables)} codelists")


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(check())
    main()
