"""What the history looks like as programmes change, vanish and come back.

These run the real loader against a mutated copy of the export fixture, one load
per generation, so what is asserted is dlt's scd2 behaviour on our data rather
than a model of it. The claims that matter are the published ones: a programme
that changed keeps its original first-seen date, one that left the export keeps
its last known content, and one that came back stops being absent without losing
the record that it was.

:func:`fdb_scraper.history.fold` is tested separately and without a database --
it is pure, and the edge cases are cheaper to state as a frame than as four
generations of files.
"""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import dlt
import polars as pl
import pytest
from dlt.pipeline.exceptions import PipelineStepFailed

from fdb_scraper import collect, history
from fdb_scraper.contract import ContractError
from fdb_scraper.history import (
    PROGRAMME_HINTS,
    SCD2,
    SCHEMA_CONTRACT,
    VALIDITY_COLUMNS,
    _assert_unique,
    count_live,
    dlt_pipeline,
    fold,
    load,
    segment_keywords,
    snapshot,
)
from fdb_scraper.pipeline import publish
from fdb_scraper.schema import (
    EXPORT_FIELDS,
    HISTORY_COLUMNS,
    PUBLISHED_FIELDS,
    USEABLE_FIELDS,
)
from fdb_scraper.scraper import scrape

FIXTURE = Path(__file__).parent / "fixtures" / "export"
PROGRAMME_DIR = "BMWI/FDB/Content/DE/Foerderprogramm"
# Any one of the three fixture programmes; this one is mutated and removed below.
SUBJECT = f"{PROGRAMME_DIR}/Bund/BA/eingliederungszuschuss-bund.xml"


@pytest.fixture
def export(tmp_path: Path) -> Path:
    """A writable copy of the export fixture, so a generation can mutate it."""
    root = tmp_path / "export"
    shutil.copytree(FIXTURE, root)
    return root


# Postgres is what the deployment runs; DuckDB is what a local run and CI use.
# dlt generates the scd2 SQL per destination, so every behaviour asserted below is
# checked against both rather than assumed to transfer. Set FDB_TEST_POSTGRES to a
# connection string to include it; without one the Postgres runs are skipped, so
# the suite still passes with no service.
DESTINATIONS = ("duckdb", "postgres")


@pytest.fixture(params=DESTINATIONS)
def pipe(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A pipeline on its own empty database, with dlt's state kept out of the repo."""
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt"))
    # ``load`` segments new keywords values when the endpoint is configured, so a
    # developer with the tagger in their environment would otherwise have every test
    # in this file make real model calls.
    for name in ("FDB_TAGGER_URL", "FDB_TAGGER_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    if request.param == "duckdb":
        monkeypatch.delenv("POSTGRES_CONN_STR", raising=False)
        yield dlt_pipeline(tmp_path / "fdb.duckdb")
        return  # tmp_path is thrown away, so there is nothing to drop

    conn_str = os.environ.get("FDB_TEST_POSTGRES")
    if not conn_str:
        pytest.skip("set FDB_TEST_POSTGRES to run the history tests on Postgres")
    # Each test needs an empty history, and the tests assert on absolute row counts.
    # A schema per test keeps them independent without a database per test.
    schema = f"t{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(history, "DATASET_NAME", schema)
    pipe = dlt_pipeline(conn_str=conn_str)
    yield pipe
    with pipe.sql_client() as client:
        client.drop_dataset()


def retitle(root: Path, rel: str, title: str) -> None:
    """Change a programme's title in place, as an upstream edit would."""
    path = root / rel
    text = path.read_text(encoding="utf-8")
    start = text.index('name="gsb:title"')
    end = text.index("</property>", start)
    block = text[start:end]
    # The title travels as CDATA-escaped HTML; replacing the paragraph body is
    # enough to change the parsed value without disturbing the wrapper.
    body = block[block.index("&lt;p&gt;") + len("&lt;p&gt;") : block.index("&lt;/p&gt;")]
    path.write_text(text.replace(body, title, 1), encoding="utf-8")


def url_of(root: Path, rel: str) -> str:
    """The URL the scraper derives for one programme file, to key assertions on."""
    slug = rel.removeprefix(f"{PROGRAMME_DIR}/").removesuffix(".xml")
    frame = scrape(["url"], export_dir=root)
    return next(u for u in frame["url"] if u.endswith(f"{slug}.html"))


def row(df: pl.DataFrame, url: str) -> dict:
    return df.filter(pl.col("url") == url).to_dicts()[0]


def test_the_published_projection_can_change_without_touching_the_history(
    export: Path, pipe
) -> None:
    """Dropping or renaming a *published* column is free. Dropping a stored one is not.

    The two live in different places on purpose. ``fold`` derives
    ``on_website_from``, ``last_updated`` and ``previous_update_dates`` from the
    ``programmes`` versions alone, and ``publish`` only reads them -- so a change to
    ``PUBLISHED_FIELDS``, to a rename, or to any column assembled at publish time (all
    of ``contact_info_*``, which comes from the ``documents`` index) cannot put a
    timestamp in a consumer's update history.
    """
    load(export, pipe=pipe)
    before, _ = snapshot(pipe)
    full = publish(pipe=pipe)
    fewer = publish(fields=[f for f in PUBLISHED_FIELDS if f != "contact_info_fax"], pipe=pipe)

    assert "contact_info_fax" not in fewer.columns
    assert fewer.height == full.height
    after, _ = snapshot(pipe)
    assert before.equals(after), "publishing a narrower projection altered the history"
    assert after["previous_update_dates"].list.len().sum() == 0


# The columns stored per programme version. scd2 runs with
# `row_version_column_name` unset, so dlt hashes *every* column of the row it is
# given: add or remove one and all 2500 rows re-hash, every live version retires and
# re-inserts, and every programme comes out with `last_updated` set to the migration
# and a junk entry in `previous_update_dates`. Nothing detects that -- it looks
# exactly like upstream having edited the whole export in one night.
#
# So this is pinned. If a change here is deliberate, either
#   * keep storing the field and drop it from PUBLISHED_FIELDS instead, which is free
#     (see the test above), or
#   * accept the churn knowingly, and migrate the published dates: record the load id
#     of the migration and exclude its timestamp in `fold`, so the column keeps saying
#     "when upstream last changed this programme" rather than "when we refactored".
STORED_PROGRAMME_FIELDS = 48


def test_the_stored_columns_are_pinned_because_changing_them_rewrites_history() -> None:
    assert len(USEABLE_FIELDS) == STORED_PROGRAMME_FIELDS, (
        "USEABLE_FIELDS changed: every stored row will re-hash and every programme "
        "will get a spurious last_updated. Read the comment above this test."
    )
    # contact_info_* is not stored, which is why removing one is a publish-time
    # decision rather than a migration.
    assert not [f for f in USEABLE_FIELDS if f.startswith("contact_info")]


def test_an_unchanged_export_adds_no_versions(export: Path, pipe) -> None:
    """Loading the same export twice must not look like every programme changed."""
    load(export, pipe=pipe)
    first, _ = snapshot(pipe)
    load(export, pipe=pipe)
    second, _ = snapshot(pipe)

    assert first.equals(second), "a no-op load altered the published snapshot"
    assert second["previous_update_dates"].list.len().sum() == 0


def test_a_changed_value_keeps_the_original_first_seen(export: Path, pipe) -> None:
    """The whole reason first-seen is a minimum rather than the live row's value."""
    url = url_of(export, SUBJECT)
    load(export, pipe=pipe)
    before = row(snapshot(pipe)[0], url)

    retitle(export, SUBJECT, "Ein anderer Titel")
    load(export, pipe=pipe)
    after = row(snapshot(pipe)[0], url)

    assert after["title"] == "Ein anderer Titel", "the new value is not published"
    assert after["on_website_from"] == before["on_website_from"], (
        "first-seen moved when the programme merely changed"
    )
    assert len(after["previous_update_dates"]) == 1, "the change was not recorded"
    assert after["last_updated"] == after["previous_update_dates"][0]
    assert not after["absent"]
    assert after["on_website_to"] is None, "active programme has on_website_to set"


def test_a_vanished_programme_keeps_its_last_known_content(export: Path, pipe) -> None:
    """Leaving the export must not erase what the programme said."""
    url = url_of(export, SUBJECT)
    load(export, pipe=pipe)
    before = row(snapshot(pipe)[0], url)

    (export / SUBJECT).unlink()
    load(export, pipe=pipe)
    frame, _ = snapshot(pipe)
    after = row(frame, url)

    assert after["absent"], "a programme absent from the export is not flagged"
    assert after["title"] == before["title"], "content was lost on absence"
    assert after["on_website_from"] == before["on_website_from"]
    assert after["on_website_to"] is not None, "no record of when it left"
    # last_updated tracks content changes, not absence, so it should remain null
    # if the programme never changed before leaving
    assert after["last_updated"] == before["last_updated"]
    # The point of keeping it: the row count does not drop when upstream removes
    # something, so a consumer can tell "withdrawn" from "never existed".
    assert len(frame) == 3


def test_a_returning_programme_stops_being_absent(export: Path, pipe) -> None:
    """Reappearing clears the flag without discarding the history of the gap."""
    url = url_of(export, SUBJECT)
    original = (export / SUBJECT).read_bytes()

    load(export, pipe=pipe)
    (export / SUBJECT).unlink()
    load(export, pipe=pipe)
    assert row(snapshot(pipe)[0], url)["absent"]

    (export / SUBJECT).write_bytes(original)
    load(export, pipe=pipe)
    after = row(snapshot(pipe)[0], url)

    assert not after["absent"], "the programme is back but still flagged absent"
    assert after["on_website_to"] is None, "programme is back but on_website_to still set"
    # When a program returns, it creates a new version, so there IS a transition recorded.
    # The point is that the gap itself (the period of absence) is tracked, not that it's invisible.
    assert len(after["previous_update_dates"]) == 1, (
        "the gap it spent off the website was forgotten"
    )


def test_a_new_programme_is_added_without_history(export: Path, pipe) -> None:
    new = f"{PROGRAMME_DIR}/Bund/BA/ein-neues-programm.xml"
    load(export, pipe=pipe)
    shutil.copy(export / SUBJECT, export / new)
    retitle(export, new, "Ein neues Programm")

    load(export, pipe=pipe)
    frame, _ = snapshot(pipe)
    after = row(frame, url_of(export, new))

    assert len(frame) == 4
    assert not after["absent"]
    assert after["on_website_to"] is None
    assert after["previous_update_dates"] == [], "a new programme has no changes yet"
    assert after["last_updated"] is None


def test_the_stored_export_round_trips_exactly(export: Path, pipe) -> None:
    """What comes back out must be what ``scrape`` put in, dtypes included.

    Storage is lossy in two ways that would otherwise leak downstream: list
    columns become JSON text, and a column that is null throughout comes back
    untyped. Seven fixture columns are in the second case.
    """
    load(export, pipe=pipe)
    stored, _ = snapshot(pipe)
    stored = stored.drop(HISTORY_COLUMNS).sort("url")
    fresh = scrape(USEABLE_FIELDS, export_dir=export).select(stored.columns).sort("url")

    assert stored.schema == fresh.schema
    assert stored.equals(fresh)


def test_two_copies_of_a_programme_are_two_programmes(export: Path, pipe) -> None:
    """``id_hash`` comes from the URL path, so a copied file is not a duplicate.

    Worth pinning: it is why the export cannot produce the repeated key that
    ``funding_crawler`` had to repair by hand. The guard below defends the
    invariant rather than an expected condition.
    """
    duplicate = export / PROGRAMME_DIR / "Land" / "BA" / Path(SUBJECT).name
    duplicate.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(export / SUBJECT, duplicate)

    load(export, pipe=pipe)
    frame, _ = snapshot(pipe)
    assert len(frame) == 4, "a copy under a different path should be its own row"


def test_a_duplicate_key_is_refused_rather_than_loaded() -> None:
    """scd2 does not deduplicate and ``primary_key`` does not make it.

    A key yielded twice lands as two rows with open validity windows, and every
    later load keeps both alive -- the state ``funding_crawler``'s
    ``queries/fix_dupliates.sql`` exists to repair. Verified against the guard
    directly, since the export cannot construct the case.
    """
    twice = pl.DataFrame([
        {"id_hash": "a", "url": "u/a"},
        {"id_hash": "a", "url": "u/a"},
        {"id_hash": "b", "url": "u/b"},
    ])
    with pytest.raises(ValueError, match="duplicate id_hash"):
        _assert_unique(twice, "id_hash")


def test_an_undeclared_column_is_refused(export: Path, pipe) -> None:
    """The schema contract, not dlt's default of evolving silently.

    An export field nobody declared means the source changed under us. Everywhere
    else in this repo that stops the run -- ``check_export`` on structure, the
    pandera schema on values -- and it should here too, rather than leave a column
    in the history that exists for some rows and not others.
    """
    load(export, pipe=pipe)

    @dlt.resource(
        name="programmes",
        write_disposition=SCD2,
        columns=PROGRAMME_HINTS,
        max_table_nesting=0,
    )
    def drifted():
        yield {"id_hash": "0" * 32, "url": "u", "an_undeclared_field": 1}

    # dlt detects this while normalising, after extraction, so the contract error
    # arrives wrapped rather than raised directly. Matched on the message so the
    # test cannot pass on some unrelated pipeline failure.
    with pytest.raises(PipelineStepFailed, match="contract_mode=freeze"):
        pipe.run(drifted(), schema_contract=SCHEMA_CONTRACT)


def test_publish_returns_the_published_shape(export: Path, pipe) -> None:
    """``publish`` is the history-bearing counterpart to ``collect``."""
    load(export, pipe=pipe)
    df = publish(pipe=pipe)

    assert df.columns == list(PUBLISHED_FIELDS), "published column order changed"
    assert set(HISTORY_COLUMNS) <= set(df.columns)
    # collect() reads an export and cannot know any of this.
    assert collect(export_dir=export).columns == list(EXPORT_FIELDS)


def test_a_drifted_export_is_not_loaded(export: Path, pipe) -> None:
    """The structural contract has to run before the history is written.

    More important here than in ``collect``: a renamed property parses as null, and
    a null written into the history stays there -- upstream no longer serves the
    export that produced it, so there is nothing to reload from.
    """
    path = export / SUBJECT
    path.write_bytes(path.read_bytes().replace(b'name="gsb:title"', b'name="gsb:heading"'))

    with pytest.raises(ContractError):
        load(export, pipe=pipe)
    assert count_live(pipe, "programmes") == 0, "a drifted export reached the history"

    # ...and can be overridden for an export whose drift is already understood.
    load(export, pipe=pipe, check_contract=False)
    assert count_live(pipe, "programmes") == 3


def test_a_collapsed_export_is_refused(export: Path, pipe, monkeypatch) -> None:
    """A truncated download looks exactly like upstream deleting everything.

    scd2 would close those validity windows for good, so the load is rejected. The
    guard is proportional and normally dormant below MIN_GUARDED, which is why the
    floor is lowered here rather than building a 100-programme fixture.
    """
    load(export, pipe=pipe)
    monkeypatch.setattr(history, "MIN_GUARDED", 2)
    for extra in list((export / PROGRAMME_DIR).rglob("*.xml"))[1:]:
        extra.unlink()

    with pytest.raises(RuntimeError, match="looks truncated"):
        load(export, pipe=pipe)


def test_an_empty_export_is_refused(export: Path, pipe) -> None:
    """An export that parses to nothing must not retire the whole dataset."""
    load(export, pipe=pipe)
    for path in (export / PROGRAMME_DIR).rglob("*.xml"):
        path.unlink()

    # Three nets stand between an empty export and a wiped history; this asserts
    # the outermost one that actually fires. `scraper.scrape` refuses an export
    # with no programme files at all, and dlt surfaces that from the extract step,
    # so nothing reaches the destination. The empty-load-package check in `load`
    # covers the case where extraction succeeds but yields nothing.
    with pytest.raises(PipelineStepFailed, match="no programme files"):
        load(export, pipe=pipe)
    assert count_live(pipe, "programmes") == 3, "the history was retired anyway"


def _versions(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def test_fold_takes_first_seen_from_the_earliest_version() -> None:
    """A programme changed twice is as old as its first version, not its second.

    ``funding_crawler``'s query reached one retirement back, so anything that
    changed more than once was published as younger than it was.
    """
    day = lambda n: datetime(2026, 1, n, tzinfo=timezone.utc)  # noqa: E731
    frm, to = VALIDITY_COLUMNS
    folded = fold(
        _versions([
            {"id_hash": "b", "url": "u/b", "title": "B", frm: day(1), to: day(2)},
            {"id_hash": "b", "url": "u/b", "title": "B2", frm: day(2), to: day(3)},
            {"id_hash": "b", "url": "u/b", "title": "B3", frm: day(3), to: None},
        ])
    ).to_dicts()[0]

    assert folded["title"] == "B3", "the live version's content should win"
    assert folded["on_website_from"] == day(1)
    assert folded["previous_update_dates"] == [day(2), day(3)]
    assert folded["last_updated"] == day(3)
    assert not folded["absent"]
    assert folded["on_website_to"] is None


def test_fold_reports_a_programme_with_no_open_window_as_absent() -> None:
    day = lambda n: datetime(2026, 1, n, tzinfo=timezone.utc)  # noqa: E731
    frm, to = VALIDITY_COLUMNS
    folded = fold(
        _versions([
            {"id_hash": "c", "url": "u/c", "title": "C", frm: day(1), to: day(2)},
            {"id_hash": "c", "url": "u/c", "title": "C2", frm: day(2), to: day(4)},
        ])
    ).to_dicts()[0]

    assert folded["absent"]
    assert folded["title"] == "C2", "the last retired version's content is kept"
    assert folded["on_website_from"] == day(1)
    # last_updated is the last content change (day 2), not the absence (day 4)
    assert folded["last_updated"] == day(2)
    assert folded["on_website_to"] == day(4)
    # previous_update_dates excludes the absence transition
    assert folded["previous_update_dates"] == [day(2)]


# --- the inferred column's materialised input ---------------------------------


class StubTagger:
    """Segments on whitespace: a valid partition, and no model or network involved.

    The real endpoint's judgement is measured in services/keyword_segmenter; what
    matters here is that its output is stored once, keyed on the string, and read
    back by ``publish`` -- none of which needs the model to be right.
    """

    def __init__(self, revision: str = "code0.weights0") -> None:
        self.calls: list[list[str]] = []
        self._revision = revision

    def revision(self) -> str:
        """What the endpoint would report. Settable, so a redeploy can be simulated."""
        return self._revision

    def segment(self, values: list[str]) -> list[dict]:
        self.calls.append(list(values))
        return [{"terms": value.split(), "model": "stub"} for value in values]

    def close(self) -> None:  # pragma: no cover -- only the owned client is closed
        pass


def test_keywords_extracted_is_null_until_the_segmenter_runs(export: Path, pipe) -> None:
    """A dataset published without the tagger is still publishable.

    Which is what makes the column safe to add: the load records the export whether
    or not a model service is reachable, and an unsegmented value publishes as absent
    rather than as a guess.
    """
    load(export, pipe=pipe)
    df = publish(pipe=pipe)

    assert "keywords_extracted" in df.columns
    assert df["keywords_extracted"].null_count() == df.height
    assert df["keywords"].null_count() < df.height, "the fixture has keywords to split"


def test_a_segmentation_run_publishes_the_split_keywords(export: Path, pipe) -> None:
    load(export, pipe=pipe)
    stub = StubTagger()
    result = segment_keywords(pipe, client=stub)

    with_keywords = publish(pipe=pipe)["keywords"].is_not_null().sum()
    assert result["segmented"] == len(stub.calls[0]) == result["values"]
    published = publish(pipe=pipe)
    extracted = published.filter(pl.col("keywords_extracted").is_not_null())
    assert extracted.height == with_keywords
    # Validated on the way out, so the span invariant has held over the whole frame.
    first = extracted.row(0, named=True)
    assert list(first["keywords_extracted"]) == first["keywords"].split()


def test_a_second_segmentation_run_sends_nothing(export: Path, pipe) -> None:
    """Content-addressed and skipped when known: a rerun costs one query.

    The reason the endpoint stays affordable on a weekly schedule -- and the reason
    a publish after a processing fix does not re-run a model over 2341 strings.
    """
    load(export, pipe=pipe)
    stub = StubTagger()
    segment_keywords(pipe, client=stub)
    again = segment_keywords(pipe, client=stub)

    assert len(stub.calls) == 1, "already-segmented strings were sent again"
    assert again["segmented"] == 0
    assert again["stored"] == again["values"]


def test_an_unchanged_keywords_value_is_not_resegmented(export: Path, pipe) -> None:
    """The key is the string, so an edit elsewhere in the programme changes nothing."""
    load(export, pipe=pipe)
    stub = StubTagger()
    segment_keywords(pipe, client=stub)

    retitle(export, SUBJECT, "Ein anderer Titel")
    load(export, pipe=pipe)
    assert segment_keywords(pipe, client=stub)["segmented"] == 0
    assert len(stub.calls) == 1


def test_an_improved_segmenter_resegments_the_whole_column(export: Path, pipe) -> None:
    """The failure this prevents, at the level where it actually happened.

    Function-word glue fixed the agency names, the deploy went out, and the pipeline
    published the old splits anyway: every value was already in ``keyword_segments``,
    so nothing was sent. Skipping is now conditional on the revision matching.
    """
    load(export, pipe=pipe)
    stub = StubTagger()
    first = segment_keywords(pipe, client=stub)
    assert first["resegmented"] == 0

    improved = StubTagger(revision="code1.weights0")
    again = segment_keywords(pipe, client=improved)

    assert again["segmented"] == first["values"], "a redeploy reached none of the column"
    assert again["resegmented"] == first["values"]
    # Replaced, not duplicated: the row identity is still the string.
    assert again["stored"] == first["stored"]


def test_a_rerun_on_the_same_revision_still_sends_nothing(export: Path, pipe) -> None:
    """Re-segmentation is triggered by a changed revision, not by every run."""
    load(export, pipe=pipe)
    stub = StubTagger()
    segment_keywords(pipe, client=stub)
    again = segment_keywords(pipe, client=stub)
    assert len(stub.calls) == 1
    assert again["segmented"] == 0


def test_rows_written_before_revisions_existed_count_as_stale(export: Path, pipe) -> None:
    """Which is what makes the deployed table self-healing rather than needing a DELETE."""
    from fdb_scraper.history import KEYWORD_TABLE, stored_revisions

    load(export, pipe=pipe)
    segment_keywords(pipe, client=StubTagger())
    with pipe.sql_client() as sql:
        sql.execute_sql(
            f"UPDATE {sql.make_qualified_table_name(KEYWORD_TABLE)} SET revision = NULL"
        )
    assert set(stored_revisions(pipe).values()) == {None}

    stub = StubTagger()
    assert segment_keywords(pipe, client=stub)["resegmented"] > 0


def test_publish_reads_the_stored_segmentation_rather_than_the_model(
    export: Path, pipe
) -> None:
    """Two publishes of one history have to agree, and inference does not.

    Model output is not bit-reproducible across container generations or batch
    composition, so the published column is read from what was materialised -- the
    same reason the raw export is stored rather than refetched.
    """
    load(export, pipe=pipe)
    segment_keywords(pipe, client=StubTagger())

    assert publish(pipe=pipe).equals(publish(pipe=pipe))
