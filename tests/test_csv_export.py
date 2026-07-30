"""The CSV writer flattens nested columns by JSON-encoding them in their cell.

Polars refuses to write List/Struct columns to CSV directly. This module gets
around that by serialising each nested value to its JSON-string form, so the
rest of the pipeline can call ``df.write_csv`` unchanged.
"""
from __future__ import annotations

import json

import polars as pl

from fdb_scraper.csv_export import flatten_nested


def test_plain_columns_pass_through_unchanged() -> None:
    df = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    out = flatten_nested(df)
    assert out.schema == df.schema
    assert out.equals(df)


def test_list_of_string_columns_become_json_strings() -> None:
    df = pl.DataFrame({"a": [["x", "y"], [], ["z"]]})
    out = flatten_nested(df)
    assert out.schema["a"] == pl.String
    assert json.loads(out["a"][0]) == ["x", "y"]
    assert json.loads(out["a"][1]) == []
    assert json.loads(out["a"][2]) == ["z"]


def test_list_of_struct_columns_become_json_strings() -> None:
    df = pl.DataFrame(
        {"a": [[{"url": "u1", "title": "t1"}], [], [{"url": "u2", "title": "t2"}]]}
    )
    out = flatten_nested(df)
    assert out.schema["a"] == pl.String
    assert json.loads(out["a"][0]) == [{"url": "u1", "title": "t1"}]
    assert json.loads(out["a"][1]) == []
    assert json.loads(out["a"][2]) == [{"url": "u2", "title": "t2"}]


def test_list_of_datetime_columns_become_json_strings() -> None:
    df = pl.DataFrame(
        {
            "a": pl.Series(
                "a",
                [[], ["2024-01-01T00:00:00"]],
                dtype=pl.List(pl.Datetime("us")),
            )
        }
    )
    out = flatten_nested(df)
    assert out.schema["a"] == pl.String
    assert json.loads(out["a"][0]) == []
    assert json.loads(out["a"][1]) == ["2024-01-01T00:00:00"]


def test_null_nested_values_become_null_strings() -> None:
    df = pl.DataFrame({"a": pl.Series("a", [None, ["x"]], dtype=pl.List(pl.String))})
    out = flatten_nested(df)
    assert out["a"][0] is None
    assert json.loads(out["a"][1]) == ["x"]


def test_mixed_columns_only_nested_are_serialised() -> None:
    df = pl.DataFrame(
        {"a": [1, 2], "b": [["x"], ["y"]], "c": ["p", "q"], "d": [True, False]}
    )
    out = flatten_nested(df)
    assert out["a"].to_list() == [1, 2]
    assert json.loads(out["b"][0]) == ["x"]
    assert json.loads(out["b"][1]) == ["y"]
    assert out["c"].to_list() == ["p", "q"]
    assert out["d"].to_list() == [True, False]


def test_writes_csv_via_polars() -> None:
    """The whole point: a DataFrame with nested columns round-trips through CSV."""
    df = pl.DataFrame({"a": [1, 2], "b": [["x", "y"], []], "c": ["p", "q"]})
    csv_df = flatten_nested(df)
    csv_bytes = csv_df.write_csv()
    # Round-trip via pl.read_csv: nested column is now String
    rt = pl.read_csv(csv_bytes.encode())
    assert rt["a"].to_list() == [1, 2]
    assert json.loads(rt["b"][0]) == ["x", "y"]
    assert json.loads(rt["b"][1]) == []
    assert rt["c"].to_list() == ["p", "q"]