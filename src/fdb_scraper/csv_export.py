"""Write a published DataFrame as CSV, with a documented encoding for nested types.

Polars refuses to write list/struct/datetime-of-list columns directly to CSV, so
nested values are pre-encoded as JSON strings in their cell. Plain columns pass
through unchanged.

The encoding convention, per cell type:

    pl.List(String)       -> ``'["a","b"]'``
    pl.List(Struct)       -> ``'[{"k":"v"}]'``
    pl.List(Datetime)     -> ``'["2024-01-01T00:00:00Z"]'``
    pl.String             -> unchanged
    pl.Int*, Float*, Bool -> unchanged

It is JSON in a cell: a consumer reads the CSV, picks the columns the published
schema declares as lists, and ``json.loads()`` each cell. The convention is
declared in the DCAT table-schema so a consumer knows which columns to parse.

Nothing in the pipeline writes a file; this module only returns the DataFrame
in a form CSV can carry.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import polars as pl


def _default(obj):
    """JSON encoder for values polars puts inside lists: datetimes."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def flatten_nested(df: pl.DataFrame) -> pl.DataFrame:
    """Return ``df`` with every nested column replaced by its JSON-string form.

    Plain columns are returned by reference (no copy). The schema after this call
    is all primitive types and can be written with ``df.write_csv`` directly.
    """

    def to_json(series: pl.Series) -> pl.Series:
        return pl.Series(
            [json.dumps(v, default=_default) if v is not None else None
             for v in series.to_list()]
        )

    nested = [name for name, dtype in df.schema.items() if _is_nested(dtype)]
    if not nested:
        return df
    return df.with_columns(
        [
            pl.col(name).map_batches(to_json, return_dtype=pl.String).alias(name)
            for name in nested
        ]
    )


def _is_nested(dtype: pl.DataType) -> bool:
    """A column is "nested" iff it cannot be written to CSV as-is."""
    s = str(dtype)
    return "List" in s or "Struct" in s or "Array" in s