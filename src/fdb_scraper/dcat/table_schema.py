"""The CSVW column contract, and the check that a written CSV matches it."""

from __future__ import annotations

from pathlib import Path

from fdb_scraper.dcat.columns import datatype_of, description_of
from fdb_scraper.config import (
    DATASET_TITLE,
    ORIGIN_TERM,
    PRIMARY_KEY,
    RECORD_URI,
    TABLE_SCHEMA_DESCRIPTION_DE,
    VOCAB,
)
from fdb_scraper.schema import COLUMNS, ORIGIN, PUBLISHED_FIELDS
from fdb_scraper.semantics import PREDICATES, expand


def build_table_schema() -> dict:
    """The column contract, generated from the pandera schema.

    CSVW's ``tableSchema`` with no ``url``: a schema, not a description of one
    file. CSV carries no column types at all, so this is what tells a consumer
    which predicate a column denotes, which are required, what pattern a value
    matches and which range a date falls in. DCAT-AP.de has no column-level
    vocabulary of its own, so CSVW is used as the least surprising container for
    that contract.

    Nested columns (lists, structs, lists of datetimes) carry JSON-encoded
    values inside their cell. The column-level ``dc:description`` states which
    cells a consumer must ``json.loads()``.

    Every column also carries ``fdb:origin`` -- ``upstream``, ``derived`` or
    ``inferred``. CSVW has no term for it and no CSV can show it, and one published
    column is a model's reading of an upstream string rather than something the
    export states; a consumer treating that value as a fact of the source would be
    wrong in a way nothing else in the file corrects.
    """
    columns = []
    for name in PUBLISHED_FIELDS:
        col = COLUMNS[name]
        entry: dict = {
            "name": name,
            "titles": name,
            "propertyUrl": expand(PREDICATES[name]),
            ORIGIN_TERM: ORIGIN[name],
        }
        if (datatype := datatype_of(name)) is not None:
            entry["datatype"] = datatype
        # Stated on every column, both ways. CSVW defaults ``required`` to false,
        # so a nullable column could be left silent -- but then "empty cell is
        # allowed here" and "nobody said" look identical to a consumer. The
        # table-level ``null`` says which token means null; this says where it may
        # appear.
        entry["required"] = not col.nullable
        # No valueUrl on the closed-vocabulary columns. It would have to name a URI
        # per code, and the codes are published as codes: a value is checked
        # against fdb_scraper.generated.vocab, not against a scheme anyone can dereference.
        if (description := description_of(name)) is not None:
            entry["dc:description"] = description
        columns.append(entry)

    return {
        # fdb: is bound here so the per-column fdb:origin resolves to the minted
        # term rather than being an undefined key a consumer has to guess at.
        "@context": [
            "http://www.w3.org/ns/csvw",
            {"@language": "de", "fdb": VOCAB},
        ],
        # The dataset's German title, not a second one: the contract and the
        # dataset it constrains are the same thing to a consumer.
        "dc:title": DATASET_TITLE["de"],
        "dc:description": TABLE_SCHEMA_DESCRIPTION_DE,
        "tableSchema": {
            "primaryKey": PRIMARY_KEY,
            "aboutUrl": RECORD_URI,
            "null": "",
            "columns": columns,
        },
    }


def check_csv(path: Path) -> list[str]:
    """Report where a written CSV contradicts the generated schema.

    Reads the header only, so it costs nothing to run as a publish gate. Checks
    what the metadata asserts and the writer can be wrong about: the columns
    present and their order. Values are the pandera schema's job and are
    already checked before anything is written.
    """
    problems = []
    with path.open() as f:
        header = f.readline().rstrip("\n").rstrip("\r").split(",")
    columns = [c for c in header if c]
    if columns != list(PUBLISHED_FIELDS):
        missing = set(PUBLISHED_FIELDS) - set(columns)
        extra = set(columns) - set(PUBLISHED_FIELDS)
        if missing or extra:
            problems.append(
                f"column mismatch: missing {sorted(missing)}, extra {sorted(extra)}"
            )
        else:
            problems.append("columns are in a different order than PUBLISHED_FIELDS")
    return problems
