"""Apply the contract in :mod:`fdb_scraper.schema` to what the parser returns.

Transforms only -- every declaration lives in :mod:`fdb_scraper.schema`, so which
fields exist and what they are called is answered there and nowhere else. In the
order :func:`process` applies them:

``decode``
    Values only, in place. The export states a category as a path into the CMS's
    classifier tree, so ``target:/BMWI/SiteGlobals/Categories/FDB/Foerderart/
    zuschuss`` becomes ``zuschuss``, and the duplicate nationwide category is
    collapsed. Same columns in, same columns out.

``add_identifiers``
    ``id_url`` and ``id_hash``, derived from ``url``.

``add_links``
    ``contact_info_*`` and ``further_links``, resolved against the linked-document
    index :func:`fdb_scraper.links.resolve` builds. This is why the raw frame is
    needed as well as the decoded one -- the hrefs it joins on are exactly what
    ``decode`` strips to leaf labels.

``collapse_pivots``
    :data:`fdb_scraper.schema.PIVOTS` applied: 21 columns to 2.

``drop`` and ``rename``
    ``CONSUMED_FIELDS`` out, ``RENAMES`` applied. The rename is last, so every
    step before it is keyed on export names.

``add_keyword_segments``
    ``keywords_extracted``, looked up in an already-computed mapping. After the
    rename, and last, because it is the only step whose input is not the export.

50 columns in, 47 out, 48 with the segmentation. The module is not called
"publish": publishing is what ``dcat/`` and the distributions do, and nothing here
writes anything.
"""

from __future__ import annotations

import hashlib

import polars as pl

from fdb_scraper.links import CONTACT_KEYS
from fdb_scraper.schema import (
    CODE_ALIASES,
    CONSUMED_FIELDS,
    CONTACT_PREFIX,
    PIVOTS,
    RENAMES,
    SEPARATOR,
    SOURCE_LICENCE,
    SOURCE_LICENCE_URL,
    SOURCE_LICENSOR,
)

_LEAF = r"([^/]+)$"


def decode(df: pl.DataFrame) -> pl.DataFrame:
    """Reduce classifier link lists to their leaf labels.

    Raw values look like ``target:/BMWI/SiteGlobals/Categories/FDB/GRW/grw_foerderung``
    -- the prefix is a constant, only the final segment carries information.
    Duplicate categories listed in :data:`CODE_ALIASES` are collapsed.
    """
    df = df.with_columns(
        pl.col(c).list.eval(pl.element().str.extract(_LEAF))
        for c, dtype in df.schema.items()
        if dtype == pl.List(pl.String)
    )
    return df.with_columns(
        # unique() because a programme may carry both sides of an alias.
        pl.col(c).list.eval(pl.element().replace(aliases)).list.unique(maintain_order=True)
        for c, aliases in CODE_ALIASES.items()
        if df.schema.get(c) == pl.List(pl.String)
    )


def _id_url(url: str) -> str:
    """Slug identifying a programme, matching the previously published form."""
    return url.partition("Foerderprogramm/")[2].removesuffix(".html").replace("/", "-").lower()


def add_identifiers(df: pl.DataFrame) -> pl.DataFrame:
    id_url = pl.col("url").map_elements(_id_url, return_dtype=pl.String)
    return df.with_columns(
        id_url.alias("id_url"),
        id_url.map_elements(
            lambda s: hashlib.md5(s.encode()).hexdigest(), return_dtype=pl.String
        ).alias("id_hash"),
    )


def add_license(df: pl.DataFrame) -> pl.DataFrame:
    """The attribution line a reuser of one row has to reproduce.

    CC BY-ND requires naming the work, its rights holder, the licence and the
    source. A consumer holding a single row -- one programme pulled out of the
    CSV -- has no way to assemble that from the dataset-level metadata, so each
    row carries its own.

    No retrieval date, unlike ``funding_crawler``'s version. A date baked in here
    would be the publish date, which is wrong for a withdrawn programme: it was last
    retrieved when it left the export, not when the file was written. The row already
    carries ``on_website_from`` and ``last_updated``, and the published metadata
    carries ``dct:modified``, so the date is stated where it can be stated correctly.
    """
    # Three programmes ship no title; id_url stands in so the attribution still
    # names something rather than reading "None von ...".
    subject = pl.coalesce(pl.col("title"), pl.col("id_url"))
    return df.with_columns(
        pl.format(
            "{} von {}, lizensiert unter {} ({}), Quelle: {}",
            subject,
            pl.lit(SOURCE_LICENSOR),
            pl.lit(SOURCE_LICENCE),
            pl.lit(SOURCE_LICENCE_URL),
            pl.col("url"),
        ).alias("license_info")
    )


def add_links(df: pl.DataFrame, raw: pl.DataFrame, docs: dict[str, dict]) -> pl.DataFrame:
    """Attach resolved contact details and external links.

    Joins on ``raw``, which still holds full document hrefs; ``df`` has been
    processed down to leaf labels by then.
    """
    empty = {f: None for f in CONTACT_KEYS}

    # Hrefs are "target:/BMWI/..."; the index is keyed on the path alone.
    def key(href: str) -> str:
        return href.removeprefix("target:")

    # 2464 of 2500 programmes link exactly one contact and 33 link two, so the
    # flat columns describe the first; contact_ids keeps the rest recoverable.
    resolved = [
        docs.get(key(hrefs[0]), empty) if hrefs else empty
        for hrefs in raw["kontakt"].to_list()
    ]
    # A handful of links target /BMWI/FDB/Schulung/_Schulungsleiter, an internal
    # training tree rather than public content. They resolve to nothing, so they
    # are left out instead of emitting a dead URL.
    links = [
        [
            {"url": docs[k]["url"], "title": docs[k].get("institution")}
            for h in (hrefs or [])
            if (k := key(h)) in docs
        ]
        for hrefs in raw["externer_link"].to_list()
    ]

    return df.with_columns(
        pl.Series(
            f"{CONTACT_PREFIX}{f}", [r.get(f) for r in resolved], dtype=pl.String
        )
        for f in empty
    ).with_columns(
        pl.Series(
            "further_links",
            links,
            dtype=pl.List(pl.Struct({"url": pl.String, "title": pl.String})),
        )
    )


def collapse_pivots(df: pl.DataFrame) -> pl.DataFrame:
    """Collapse each pivoted taxonomy into one column of paths, dropping the sources.

    Lossless: every (parent, child) pair stays distinct. The parent is *not*
    checked against the column it pivots on -- 132 of 5963 sub-area values sit on
    a programme that does not list their Förderbereich, and one has no
    ``funding_area`` at all, so requiring it would reject real upstream data.
    Notebook E11 measures it.
    """
    out = df
    for target, parents in PIVOTS.items():
        present = [c for c in parents if c in out.columns]
        if not present:
            continue
        out = out.with_columns(
            pl.concat_list(
                pl.col(column).list.eval(
                    pl.lit(f"{parents[column]}{SEPARATOR}") + pl.element()
                )
                for column in present
            )
            .list.drop_nulls()
            .alias(target)
        ).drop(present)
    return out

def add_keyword_segments(
    df: pl.DataFrame, segments: dict[str, list[str]]
) -> pl.DataFrame:
    """``keywords_extracted``: the raw keywords string cut into keywords.

    A dict lookup, exactly as ``add_links`` looks up documents -- the model call
    happens in :func:`fdb_scraper.history.segment_keywords` and is materialised in
    the database, so publishing stays offline, deterministic and rerunnable. Model
    inference is not bit-reproducible across container generations, so recomputing
    here would make two publishes of the same history differ.

    Keyed on the raw string rather than on a programme, because the segmentation is
    a function of the string alone: 2440 non-null values collapse to 2341 distinct
    ones, and a programme whose keywords never change is never re-segmented.

    Null where the string is null, and null where it has one but no segmentation
    has been materialised for it -- a value the segmenter has not seen publishes as
    absent rather than as a guess.
    """
    values = df["keywords"].to_list()
    return df.with_columns(
        pl.Series(
            "keywords_extracted",
            [None if v is None else segments.get(v) for v in values],
            dtype=pl.List(pl.String),
        )
    )


def process(
    raw: pl.DataFrame,
    documents: dict[str, dict] | None = None,
    segments: dict[str, list[str]] | None = None,
) -> pl.DataFrame:
    """Decode and reshape ``raw`` into the published table.

    Args:
        raw: Output of :func:`fdb_scraper.scrape`, with export names and the
            classifier hrefs still intact.
        documents: The linked-document index from
            :func:`fdb_scraper.links.resolve`. Omit to skip ``add_links``, which
            is what ``scripts/gen_vocab.py`` wants: it needs the decoded category
            values and nothing else.
        segments: Raw keywords string -> its keywords, from
            :func:`fdb_scraper.history.segments`. Omit to leave
            ``keywords_extracted`` off the frame entirely, which is what
            :func:`fdb_scraper.collect` does: nothing in a single export can
            supply it, the same reason the history columns are absent there.
    """
    df = decode(raw)
    df = add_identifiers(df)
    # After add_identifiers: the attribution falls back to id_url when a programme
    # ships no title, so the identifiers have to exist first.
    df = add_license(df)
    if documents is not None:
        df = add_links(df, raw, documents)
    df = collapse_pivots(df)
    df = df.drop(CONSUMED_FIELDS & set(df.columns)).rename(
        {k: v for k, v in RENAMES.items() if k in df.columns}
    )
    if segments is not None:
        df = add_keyword_segments(df, segments)
    return df
