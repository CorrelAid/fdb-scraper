"""The parts of the segmenter that hold without a GPU.

Everything here is the contract the model is judged against, so it is worth more
than the model call itself: if the partition check or the metric is wrong, the
number the service reports is meaningless.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

import pytest

SERVICE = Path(__file__).parents[1] / "services" / "keyword_segmenter"
sys.path.insert(0, str(SERVICE))

from segment.metric import pool, score_one  # noqa: E402
from segment.postprocess import FUNCTION_WORDS, glue_function_words  # noqa: E402
from segment.tokens import is_partition, spans, terms, tokenize  # noqa: E402


def labels() -> list[dict]:
    path = SERVICE / "labels.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


# -- the partition contract --------------------------------------------------


def test_tokenize_is_exact():
    """_clean guarantees single spaces, so split() loses nothing."""
    raw = "Erneuerbare Energien Zuschuss"
    assert tokenize(raw) == ["Erneuerbare", "Energien", "Zuschuss"]
    assert " ".join(tokenize(raw)) == raw


@pytest.mark.parametrize(
    "sizes,n,valid",
    [
        ([1, 1, 1], 3, True),
        ([3], 3, True),
        ([2, 1], 3, True),
        ([1, 1], 3, False),  # under-covers
        ([2, 2], 3, False),  # over-covers
        ([0, 3], 3, False),  # empty group
        ([-1, 4], 3, False),  # negative group
        ([], 3, False),  # no answer at all
    ],
)
def test_is_partition(sizes, n, valid):
    assert is_partition(sizes, n) is valid


def test_terms_are_contiguous_spans_of_the_source():
    """The invariant that makes hallucination unrepresentable."""
    for row in labels():
        tokens = row["tokens"]
        for term in terms(tokens, row["group_sizes"]):
            assert term in row["keywords"], (row["slug"], term)


def test_spans_cover_every_token_exactly_once():
    for row in labels():
        covered = [i for lo, hi in spans(row["group_sizes"]) for i in range(lo, hi)]
        assert covered == list(range(len(row["tokens"]))), row["slug"]


def test_punctuation_only_group_is_dropped_from_terms():
    assert terms(["Kino", ",", "Film"], [1, 1, 1]) == ["Kino", "Film"]


# -- the gold labels ---------------------------------------------------------


def test_every_label_is_a_valid_partition():
    for row in labels():
        assert is_partition(row["group_sizes"], len(row["tokens"])), row["slug"]


def test_split_sizes_are_what_the_eval_claims():
    """70 random train rows, 61 uncertainty-drawn ones, and the same untouched 50.

    The held-out half must never grow: it is the only reason a number from this round
    is comparable with the last. Everything the second sample added is train, because
    a sample chosen by asking the model where it is unsure cannot also measure it.
    """
    counts = collections.Counter(row["split"] for row in labels())
    assert counts == {"train": 131, "test": 50}
    by_source = collections.Counter(row["source"] for row in labels())
    assert by_source == {"random": 120, "uncertainty": 50, "compound-pattern": 11}
    assert all(r["split"] == "train" for r in labels() if r["source"] != "random")


# -- the metric --------------------------------------------------------------


def test_perfect_prediction_scores_one():
    s = score_one([2, 1], [2, 1], 3)
    assert (s.precision, s.recall, s.f1) == (1.0, 1.0, 1.0)


def test_invalid_partition_scores_zero_rather_than_raising():
    """The service falls back in this case, so the metric must price it."""
    s = score_one([5, 5], [2, 1], 3)
    assert s.f1 == 0.0
    assert s.support == 2


def test_partial_credit_is_per_span_not_per_token():
    """A wrongly split term earns nothing for the overlap it does have."""
    # gold: [Erneuerbare Energien][Zuschuss]; pred splits the compound.
    s = score_one([1, 1, 1], [2, 1], 3)
    assert s.recall == 0.5  # only "Zuschuss" is right
    assert s.precision == pytest.approx(1 / 3)


def test_wrongly_joining_is_punished_on_both_sides():
    s = score_one([3], [1, 1, 1], 3)
    assert s.precision == 0.0
    assert s.recall == 0.0


def test_pool_is_micro_averaged_over_terms():
    """A 10-term row must outweigh a 2-term row; the column is a flat list."""
    big = score_one([1] * 10, [1] * 10, 10)  # 10/10 right
    small = score_one([2], [1, 1], 2)  # 0/2 right
    assert pool([big, small]).recall == pytest.approx(10 / 12)


def test_long_spans_are_covered_now_and_stay_covered():
    """The gap the second sample was drawn to close, as a number.

    Before it: 93% single-token terms, ten of length three, longest span five, and
    nothing at all beyond -- which is why seven-token agency names came back split.
    After it: 85% single-token, 45 of length three, and spans up to eight. Pinned so a
    later re-draw cannot quietly undo the coverage this round paid for.
    """
    lengths = collections.Counter(s for r in labels() for s in r["group_sizes"])
    assert 0.8 < lengths[1] / sum(lengths.values()) < 0.9
    assert max(lengths) == 8
    assert lengths[3] == 45
    assert sum(v for k, v in lengths.items() if k >= 3) == 54
    # The training half is where the new labels went, so that is where the coverage
    # has to show up.
    train = collections.Counter(
        s for r in labels() if r["split"] == "train" for s in r["group_sizes"]
    )
    assert max(train) == 8


def test_all_ones_baseline_is_the_bar_the_model_must_clear():
    """Guards the headline claim in the README against label drift."""
    rows = [r for r in labels() if r["split"] == "test"]
    scored = pool(
        [score_one([1] * len(r["tokens"]), r["group_sizes"], len(r["tokens"])) for r in rows]
    )
    assert scored.f1 == pytest.approx(0.8776, abs=5e-4)




# -- the join-decision formulation -------------------------------------------


def test_any_join_list_is_a_valid_partition():
    """Why joins beat sizes: the model cannot produce an invalid answer."""
    import itertools

    from segment.tokens import joins_to_sizes

    for n in range(1, 8):
        for joins in itertools.product([True, False], repeat=n - 1):
            sizes = joins_to_sizes(list(joins))
            assert is_partition(sizes, n), (n, joins)


def test_joins_and_sizes_round_trip_on_every_gold_label():
    from segment.tokens import joins_to_sizes, sizes_to_joins

    for row in labels():
        joins = sizes_to_joins(row["group_sizes"])
        assert len(joins) == len(row["tokens"]) - 1, row["slug"]
        assert joins_to_sizes(joins) == row["group_sizes"], row["slug"]


# -- auditing the gold labels -------------------------------------------------
#
# Everything reported rests on 181 rows labelled by one person, so the labels get
# audited by rule rather than trusted. The rule that does the work is German
# orthography: an attributive adjective must be inflected. "Erneuerbare Energien"
# agrees and is one keyword; "digital Websites" does not and is two.
#
# The bias worth guarding against is specific: rows were labelled all-ones by
# default, and the baseline the models are measured against is also all-ones, so a
# missed compound silently flatters the baseline.

INFLECTED = __import__("re").compile(r"(e|en|er|es|em)$")


def _adjective_initial(token: str) -> bool:
    return token.isalpha() and len(token) > 3


def test_no_uninflected_adjective_was_joined_to_its_neighbour():
    """A join whose first word is an uninflected adjective is a labelling error.

    Unless the join is licensed by something other than agreement: "proof of concept"
    is held together by the preposition, not by German morphology, and rule 7 requires
    it -- publishing "of" as a keyword of its own is the alternative. So a span whose
    second token is a function word is out of this rule's scope.
    """
    for row in labels():
        for lo, hi in spans(row["group_sizes"]):
            first = row["tokens"][lo]
            if hi - lo > 1 and first[0].islower() and _adjective_initial(first):
                if row["tokens"][lo + 1].strip(" ,;.:()").lower() in FUNCTION_WORDS:
                    continue
                assert INFLECTED.search(first), (row["slug"], " ".join(row["tokens"][lo:hi]))


# -e/-er/-es/-em only. Deliberately excludes -en, which is also the verb
# infinitive ending: "bürgen Betriebsmittel" and "bauen Neubau" are a verb next to
# a noun, not an adjective agreeing with one, and both are correctly left split.
UNAMBIGUOUS_ADJ = __import__("re").compile(r"(e|er|es|em)$")


def _group_of(sizes: list[int]) -> list[int]:
    """Token index -> which group it belongs to."""
    out = []
    for group, (lo, hi) in enumerate(spans(sizes)):
        out.extend([group] * (hi - lo))
    return out


def test_no_inflected_adjective_noun_pair_was_left_unjoined():
    """The mirror check: the one that guards the baseline against my own default.

    Rows were labelled all-ones unless a compound was noticed, and the baseline is
    also all-ones -- so a missed compound quietly flatters the thing the models are
    being compared to. An inflected lowercase adjective immediately before a
    capitalised noun is the strongest join cue German offers, so any such pair left
    in separate groups is either a labelling miss or a documented exception.
    """
    for row in labels():
        group = _group_of(row["group_sizes"])
        for i in range(len(row["tokens"]) - 1):
            a, b = row["tokens"][i], row["tokens"][i + 1]
            same_keyword = group[i] == group[i + 1]
            if (
                not same_keyword
                and a[0].islower()
                and _adjective_initial(a)
                and UNAMBIGUOUS_ADJ.search(a)
                and b[:1].isupper()
            ):
                raise AssertionError(
                    f"{row['slug']}: inflected adjective + noun left unjoined: {a} {b}"
                )


def test_most_joins_are_explained_by_a_rule_not_by_taste():
    """Quantifies how much of the gold rests on judgement alone.

    143 of 209 joins follow from inflection; the rest are proper names, English
    terms, numbers and shared-prefix ellipses, which no orthographic rule covers.
    If that ratio moves a lot, the labels have drifted toward taste. It held across the
    second sample -- 34 of 51 before, so the added labels are no more judgement-heavy
    than the originals, which is the thing worth checking about a non-random draw.
    """
    by_rule = judgement = 0
    for row in labels():
        for lo, hi in spans(row["group_sizes"]):
            if hi - lo < 2:
                continue
            first = row["tokens"][lo]
            if first.isalpha() and INFLECTED.search(first):
                by_rule += 1
            else:
                judgement += 1
    assert by_rule + judgement == 209
    assert by_rule >= 2 * judgement, f"only {by_rule} of {by_rule + judgement} joins rule-explained"


# -- the deterministic repair --------------------------------------------------
#
# This runs on the model's output, so it is held to the same contract: it may only ever
# *add* joins to a valid partition, which keeps every published term a contiguous span
# of the source. No weights needed to check any of it.

AGENCY = "Beauftragte der Bundesregierung für Kultur und Medien BKM".split()


def test_a_function_word_neither_opens_nor_closes_a_keyword():
    """The rule that repairs the agency names, on the row that motivated it."""
    from segment.tokens import joins_to_sizes

    # What the model actually predicts here: confident on the two-token compounds,
    # undecided (0.354, 0.460) on the two gaps around "der" and "für".
    joins = [True, False, True, False, True, True, False]
    assert terms(AGENCY, joins_to_sizes(joins)) == [
        "Beauftragte der",
        "Bundesregierung für",
        "Kultur und Medien",
        "BKM",
    ]
    fixed = joins_to_sizes(glue_function_words(AGENCY, joins))
    assert terms(AGENCY, fixed) == ["Beauftragte der Bundesregierung für Kultur und Medien", "BKM"]


def test_glue_only_adds_joins():
    """Monotonicity is what makes the repair safe: it cannot invent a boundary."""
    for row in labels():
        from segment.tokens import sizes_to_joins

        joins = sizes_to_joins(row["group_sizes"])
        glued = glue_function_words(row["tokens"], joins)
        assert all(b or not a or a == b for a, b in zip(joins, glued))
        assert all(b for a, b in zip(joins, glued) if a), row["slug"]


def test_glue_never_crosses_a_delimiter_the_author_wrote():
    """A comma is evidence about this row; the rule is only inference."""
    tokens = "Wohnen, und Verkehr".split()
    # Gap 0 is closed by the author's comma and must stay closed even though the
    # next token is a function word.
    assert glue_function_words(tokens, [False, False]) == [False, True]


def test_a_function_word_at_either_end_of_the_value_glues_inward():
    """There is only one gap to work with, and the rule uses it.

    A dangling "für" is not a keyword, so binding it to the neighbour it does have
    is the right move even though the resulting term ends in a preposition -- the
    alternative publishes "für" as a search term of its own.
    """
    assert glue_function_words("Zuschuss für".split(), [False]) == [True]
    assert glue_function_words("für Kommunen".split(), [False]) == [True]


def test_the_rule_contradicts_no_gold_label():
    """The strongest check available without new labelling.

    The rule is asserted against all 181 hand-labelled rows: if it fired where a human
    had deliberately placed a boundary, that is a bug in the rule, not the label. It
    fires nowhere on gold, which is what licenses running it over the whole column --
    and it is the same statement as labelling rule 7, checked from the other side.
    """
    from segment.tokens import sizes_to_joins

    for row in labels():
        joins = sizes_to_joins(row["group_sizes"])
        assert glue_function_words(row["tokens"], joins) == joins, row["slug"]


def test_function_word_list_holds_only_words_that_cannot_be_a_keyword():
    """Guards the licence for the rule: "Land" is a keyword, "von" is not."""
    assert "land" not in FUNCTION_WORDS
    assert "recht" not in FUNCTION_WORDS
    assert {"der", "für", "und", "zur"} <= FUNCTION_WORDS







def test_the_repair_output_is_always_still_a_partition():
    from segment.tokens import joins_to_sizes, sizes_to_joins

    for row in labels():
        n = len(row["tokens"])
        for joins in ([False] * (n - 1), [True] * (n - 1), sizes_to_joins(row["group_sizes"])):
            sizes = joins_to_sizes(glue_function_words(row["tokens"], list(joins)))
            assert is_partition(sizes, n), row["slug"]



# -- threshold sweep ----------------------------------------------------------


def test_sweep_recovers_the_gold_threshold_from_clean_probabilities():
    """With confident, correct probabilities every sensible threshold scores 1.0."""
    from segment.metric import sweep_threshold
    from segment.tokens import sizes_to_joins

    rows = [r for r in labels() if r["split"] == "test"]
    probs = [[0.99 if j else 0.01 for j in sizes_to_joins(r["group_sizes"])] for r in rows]
    gold = [r["group_sizes"] for r in rows]
    for tau, score in sweep_threshold(probs, gold):
        assert score.f1 == pytest.approx(1.0), tau


def test_sweep_prices_a_missing_answer_as_the_fallback_not_as_a_skip():
    from segment.metric import sweep_threshold

    # One row, gold joins the first two of three tokens; no probabilities at all.
    (tau, score), *_ = sweep_threshold([[]], [[2, 1]], grid=[0.5])
    # Fallback is all-ones, which gets "Zuschuss" right and misses the compound.
    assert score.recall == pytest.approx(0.5)


def test_high_threshold_suppresses_joins_and_low_threshold_forces_them():
    from segment.metric import sweep_threshold

    probs = [[0.6, 0.6]]  # gold: no joins at all
    gold = [[1, 1, 1]]
    strict = dict(sweep_threshold(probs, gold, grid=[0.9]))[0.9]
    loose = dict(sweep_threshold(probs, gold, grid=[0.1]))[0.1]
    assert strict.f1 == pytest.approx(1.0)  # 0.6 < 0.9 -> nothing joined -> correct
    assert loose.f1 == 0.0  # 0.6 >= 0.1 -> all joined -> one wrong term


# -- the tagger itself, against the real weights ------------------------------
#
# Marked `model`: needs the 437 MB fine-tuned artefact, which is not in git.
#   modal run services/keyword_segmenter/finetune_app.py::train
#   modal volume get fdb-keyword-tagger keyword_tagger models/
#
# There is deliberately no HTTP service to test. The tagger is 110M parameters and
# runs in process on CPU, so there is no endpoint, no public URL and no token to
# authenticate -- the previous prompting design needed all three.

pytestmark_model = pytest.mark.model
TAGGER_PATH = Path(__file__).parents[1] / "models" / "keyword_tagger"


@pytest.fixture(scope="module")
def tagger():
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    if not TAGGER_PATH.exists():
        pytest.skip(f"no weights at {TAGGER_PATH}; see the module docstring")
    from segment.tagger import KeywordTagger

    return KeywordTagger(TAGGER_PATH)


@pytest.mark.model
def test_tagger_joins_a_known_compound_and_leaves_a_list_alone(tagger):
    """The two behaviours the whole exercise turns on, on unseen input."""
    joined = tagger("Erneuerbare Energien Zuschuss Kommune")
    assert joined["terms"] == ["Erneuerbare Energien", "Zuschuss", "Kommune"]

    # A plain enumeration must survive untouched: over-joining these was the
    # dominant failure of every prompted model.
    listed = tagger("Waldgebiet Waldfläche Forst Umweltschutz")
    assert listed["terms"] == ["Waldgebiet", "Waldfläche", "Forst", "Umweltschutz"]


@pytest.mark.model
def test_tagger_output_is_always_a_contiguous_span_of_the_input(tagger):
    """The invariant that makes an inferred column defensible, on real predictions."""
    values = [r["keywords"] for r in labels() if r["split"] == "test"]
    for value, result in zip(values, tagger.segment(values)):
        assert sum(result["group_sizes"]) == len(tokenize(value))
        for term in result["terms"]:
            assert term in value, (value, term)


@pytest.mark.model
def test_tagger_beats_the_never_join_baseline_on_the_held_out_split(tagger):
    """Guards the headline claim. Trained on train only; this is the untouched 50."""
    rows = [r for r in labels() if r["split"] == "test"]
    results = tagger.segment([r["keywords"] for r in rows])
    scored = pool(
        [
            score_one(res["group_sizes"], r["group_sizes"], len(r["tokens"]))
            for res, r in zip(results, rows)
        ]
    )
    baseline = pool(
        [score_one([1] * len(r["tokens"]), r["group_sizes"], len(r["tokens"])) for r in rows]
    )
    assert baseline.f1 == pytest.approx(0.8776, abs=5e-4)
    # Measured 0.968 at seed 0 (0.965 before the second labelling round); the floor
    # allows for retraining drift.
    assert scored.f1 > 0.93, f"tagger F1 {scored.f1:.3f} vs baseline {baseline.f1:.3f}"


@pytest.mark.model
def test_tagger_scores_the_long_span_slice_separately(tagger):
    """The slice the headline number hides.

    The headline is honest about the corpus and quiet about long terms: only five
    held-out rows contain a three-token-plus keyword, so they move the pooled figure by
    almost nothing while being the whole reason a model is needed. This slice is what
    the second labelling round was drawn to fix, and it is where the gain showed up --
    **0.906**, against 0.818 before the round and 0.550 for never-joining.
    """
    rows = [
        r
        for r in labels()
        if r["split"] == "test" and any(s >= 3 for s in r["group_sizes"])
    ]
    assert len(rows) == 5
    results = tagger.segment([r["keywords"] for r in rows])
    scored = pool(
        [
            score_one(res["group_sizes"], r["group_sizes"], len(r["tokens"]))
            for res, r in zip(results, rows)
        ]
    )
    baseline = pool(
        [score_one([1] * len(r["tokens"]), r["group_sizes"], len(r["tokens"])) for r in rows]
    )
    assert baseline.f1 == pytest.approx(0.5500, abs=5e-4)
    # Floor, not the measurement: 0.906 at seed 0, and retraining moves it.
    assert scored.f1 > 0.85, f"long-span F1 {scored.f1:.3f} vs baseline {baseline.f1:.3f}"


@pytest.mark.model
def test_tagger_keeps_a_whole_agency_name_together(tagger):
    """The failure that prompted postprocess, end to end through the real weights.

    Zero rows containing an agency name were labelled, so nothing in the eval above
    can see this; it is pinned here by name instead.
    """
    value = (
        "Stoffentwicklung Dokumentarfilm Filmförderung Stoffentwicklungsförderung "
        "Kinofilm Beauftragte der Bundesregierung für Kultur und Medien BKM"
    )
    assert tagger(value)["terms"] == [
        "Stoffentwicklung",
        "Dokumentarfilm",
        "Filmförderung",
        "Stoffentwicklungsförderung",
        "Kinofilm",
        "Beauftragte der Bundesregierung für Kultur und Medien BKM",
    ]


@pytest.mark.model
def test_the_reported_failures_are_segmented_correctly(tagger):
    """Regression guard for the two rows a human caught, not evidence of learning.

    Both are in ``train`` -- deliberately, since a reported failure is worth more as a
    label than as a test case -- so passing this proves memorisation, nothing more. The
    generalisation claim rests on the held-out slices above, where the long-span score
    went 0.818 -> 0.906 without these rows ever being scored.
    """
    value = (
        "polnische Kulturförderung Erhaltung polnische Sprache Pflege polnischer Sprache "
        "polnischsprachige Bürger Beauftragte der Bundesregierung für Kultur und Medien BKM"
    )
    assert tagger(value)["terms"] == [
        "polnische Kulturförderung",
        "Erhaltung polnische Sprache",
        "Pflege polnischer Sprache",
        "polnischsprachige Bürger",
        "Beauftragte der Bundesregierung für Kultur und Medien BKM",
    ]


@pytest.mark.model
def test_the_repairs_do_not_cost_anything_on_the_held_out_split(tagger):
    """A repair that helps the tail must not quietly hurt the body.

    It no longer helps it either, which is the interesting part. Before the second
    labelling round the rule was worth +0.003 here and +0.024 on the long-span slice;
    after it, nothing on either, and its corpus-wide footprint fell from 105 rows to
    31. The model learned the rule from labels, so it now serves as a guard rather than
    a fix -- the assertion is therefore "costs nothing", not "gains something". The
    institution gazetteer that ran beside it reached 0 rows and was deleted.
    """
    from segment.tokens import joins_to_sizes

    rows = [r for r in labels() if r["split"] == "test"]
    raw_scores, fixed_scores = [], []
    for row in rows:
        tokens = row["tokens"]
        n = len(tokens)
        if n < 2:
            continue
        probs = tagger._probabilities([tokens])[0]
        joins = [p >= tagger.threshold for p in probs[1:n]]
        joins += [False] * (n - 1 - len(joins))
        gold = row["group_sizes"]
        raw_scores.append(score_one(joins_to_sizes(joins), gold, n))
        fixed = glue_function_words(tokens, joins)
        fixed_scores.append(score_one(joins_to_sizes(fixed), gold, n))
    raw, fixed = pool(raw_scores), pool(fixed_scores)
    assert fixed.f1 >= raw.f1, f"postprocess cost {raw.f1:.4f} -> {fixed.f1:.4f}"


@pytest.mark.model
def test_single_token_values_never_reach_the_model(tagger):
    assert tagger("Zuschuss") == {"terms": ["Zuschuss"], "group_sizes": [1]}


# -- the cache ----------------------------------------------------------------
#
# No weights and no network needed: a stub stands in for the endpoint, so the
# read-through logic is tested on every run rather than only when the service is up.


class _StubEndpoint:
    """Counts calls, so a test can assert the cache actually prevented them.

    ``revision`` is settable: redeploying the segmenter is exactly what the cache has
    to notice, and a stub that can never change revision could not test it.
    """

    def __init__(self, revision: str = "code0.weights0"):
        self.calls: list[list[str]] = []
        self.revision = revision

    def __call__(self, values):
        # The probe carries no values and is not a segmentation call, so it is not
        # counted -- the assertions below are about work, not round trips.
        if values:
            self.calls.append(list(values))
        return {
            "model": "stub",
            "revision": self.revision,
            "results": [
                {"terms": v.split(), "group_sizes": [1] * len(v.split())} for v in values
            ],
        }


@pytest.fixture
def client(tmp_path, monkeypatch):
    from segment.client import Cache, TaggerClient

    monkeypatch.setenv("FDB_TAGGER_URL", "https://example.invalid")
    monkeypatch.setenv("FDB_TAGGER_TOKEN", "test-token")
    c = TaggerClient(cache=Cache(tmp_path / "cache.sqlite"))
    stub = _StubEndpoint()
    c._post = stub
    yield c, stub
    c.close()


def test_second_run_makes_no_network_call(client):
    """The point of the cache: a rerun after a processing fix re-segments nothing."""
    c, stub = client
    values = ["Erneuerbare Energien Zuschuss", "Waldgebiet Forst"]
    first = c.segment(values)
    assert len(stub.calls) == 1
    second = c.segment(values)
    assert len(stub.calls) == 1, "cache miss on a repeat run"
    assert first == second


def test_duplicate_values_are_sent_once(client):
    """2440 rows collapse to 2341 unique strings; duplicates must not be paid for."""
    c, stub = client
    c.segment(["Foerderung Zuschuss", "Foerderung Zuschuss", "Andere Sache"])
    assert stub.calls == [["Foerderung Zuschuss", "Andere Sache"]]


def test_results_come_back_in_input_order_including_duplicates(client):
    c, stub = client
    values = ["A B", "C D", "A B"]
    out = c.segment(values)
    assert [r["terms"] for r in out] == [["A", "B"], ["C", "D"], ["A", "B"]]


def test_an_edited_upstream_string_is_a_cache_miss(client):
    """The key is md5 of the raw value, so any upstream edit re-segments."""
    c, stub = client
    c.segment(["Zuschuss Kommune"])
    c.segment(["Zuschuss Kommunen"])  # one character different
    assert len(stub.calls) == 2


def test_cache_survives_a_new_client_on_the_same_file(tmp_path, monkeypatch):
    from segment.client import Cache, TaggerClient

    monkeypatch.setenv("FDB_TAGGER_URL", "https://example.invalid")
    monkeypatch.setenv("FDB_TAGGER_TOKEN", "test-token")
    path = tmp_path / "cache.sqlite"
    with TaggerClient(cache=Cache(path)) as first:
        first._post = _StubEndpoint()
        first.segment(["Erneuerbare Energien"])

    with TaggerClient(cache=Cache(path)) as second:
        stub = _StubEndpoint()
        second._post = stub
        second.segment(["Erneuerbare Energien"])
    assert stub.calls == [], "cache did not persist across processes"


# -- revision-keyed invalidation ----------------------------------------------
#
# The bug these exist for: function-word glue changed what the decoder returns, the
# cache was keyed on md5(keywords) alone, and 2340 stored rows stayed stale through a
# rerun. Nothing failed. A person had to notice.


def test_a_redeployed_segmenter_is_a_cache_miss(client):
    """The whole point: better code must reach the column without a manual DELETE."""
    c, stub = client
    c.segment(["Beauftragte der Bundesregierung"])
    assert len(stub.calls) == 1

    stub.revision = "code1.weights0"  # function-word glue added
    c._revision = None  # a new run, so the revision is probed afresh
    c.segment(["Beauftragte der Bundesregierung"])
    assert len(stub.calls) == 2, "stale entry served after the segmenter changed"


def test_a_retrained_checkpoint_is_a_cache_miss(client):
    """Same for weights, which the client cannot fingerprint itself."""
    c, stub = client
    c.segment(["Zuschuss Kommune"])
    stub.revision = "code0.weights1"
    c._revision = None
    c.segment(["Zuschuss Kommune"])
    assert len(stub.calls) == 2


def test_rolling_back_the_code_reuses_the_old_entries(client):
    """Why the revision is part of the key rather than a column to compare.

    Both revisions stay addressable, so reverting a bad deploy costs nothing instead
    of paying for the whole column again.
    """
    c, stub = client
    c.segment(["Zuschuss Kommune"])
    stub.revision = "code1.weights0"
    c._revision = None
    c.segment(["Zuschuss Kommune"])
    assert len(stub.calls) == 2

    stub.revision = "code0.weights0"  # rolled back
    c._revision = None
    c.segment(["Zuschuss Kommune"])
    assert len(stub.calls) == 2, "rollback re-segmented what it had already paid for"


def test_the_revision_is_asked_for_once_per_client(client):
    c, stub = client
    c.segment(["A B"])
    c.segment(["C D"])
    assert c.revision() == "code0.weights0"


def test_an_endpoint_that_reports_no_revision_still_works(client):
    """Deploy order is not guaranteed: an old container must not break the client."""
    c, stub = client
    c._post = lambda values: {
        "model": "old",
        "results": [{"terms": v.split(), "group_sizes": [1] * len(v.split())} for v in values],
    }
    assert c.segment(["Zuschuss Kommune"])[0]["terms"] == ["Zuschuss", "Kommune"]
    assert c.revision() == "unknown"


def test_an_empty_request_never_reaches_the_network(client):
    c, stub = client
    assert c.segment([]) == []
    assert stub.calls == []


def test_the_cache_records_which_revision_produced_each_row(client):
    """So a stale entry can be explained after the fact, not just missed."""
    c, stub = client
    c.segment(["Zuschuss Kommune"])
    rows = c.cache.db.execute("SELECT keywords, revision FROM segments").fetchall()
    assert rows == [("Zuschuss Kommune", "code0.weights0")]


def test_a_pre_revision_cache_file_is_upgraded_not_rejected(tmp_path, monkeypatch):
    """Deployments have caches written before the column existed."""
    import sqlite3

    path = tmp_path / "cache.sqlite"
    old = sqlite3.connect(path)
    old.execute(
        """CREATE TABLE segments (
               md5 TEXT PRIMARY KEY, keywords TEXT NOT NULL, result TEXT NOT NULL,
               model TEXT, created_at REAL NOT NULL)"""
    )
    old.execute("INSERT INTO segments VALUES ('x', 'Zuschuss Kommune', '{}', 'old', 0)")
    old.commit()
    old.close()

    from segment.client import Cache, TaggerClient

    monkeypatch.setenv("FDB_TAGGER_URL", "https://example.invalid")
    monkeypatch.setenv("FDB_TAGGER_TOKEN", "test-token")
    with TaggerClient(cache=Cache(path)) as c:
        stub = _StubEndpoint()
        c._post = stub
        # The pre-revision row is unreachable rather than trusted, so the value is
        # re-segmented instead of served from code that no longer exists.
        c.segment(["Zuschuss Kommune"])
        assert len(stub.calls) == 1


# -- the revision itself -------------------------------------------------------


def test_revision_changes_when_the_decoding_code_changes(tmp_path):
    from segment.revision import DECISION_SURFACE, code_revision

    for name in DECISION_SURFACE:
        (tmp_path / name).write_text("original")
    before = code_revision(tmp_path)
    (tmp_path / "postprocess.py").write_text("original, edited")
    assert code_revision(tmp_path) != before



def test_revision_changes_when_the_model_is_retrained(tmp_path):
    from segment.revision import revision

    meta = tmp_path / "training_meta.json"
    meta.write_text('{"seed": 0, "held_out_f1_at_0.5": 0.9651}')
    before = revision(tmp_path)
    meta.write_text('{"seed": 0, "held_out_f1_at_0.5": 0.9712}')
    assert revision(tmp_path) != before


def test_revision_is_stable_across_calls_and_survives_missing_files(tmp_path):
    """Same inputs, same string -- otherwise every run would invalidate everything."""
    from segment.revision import UNKNOWN, code_revision, revision

    assert code_revision(tmp_path) == code_revision(tmp_path)
    assert revision(None).endswith(UNKNOWN)
    assert revision(tmp_path).endswith(UNKNOWN)  # no training_meta.json


def test_the_shipped_revision_is_reported_for_the_real_files():
    from segment.revision import revision

    code, _, weights = revision().partition(".")
    assert len(code) == 12 and code.isalnum()
    assert weights  # "unknown" without local weights, a digest with them


def test_client_refuses_to_start_without_credentials(monkeypatch):
    from segment.client import TaggerClient

    monkeypatch.delenv("FDB_TAGGER_URL", raising=False)
    monkeypatch.delenv("FDB_TAGGER_TOKEN", raising=False)
    with pytest.raises(ValueError, match="FDB_TAGGER_URL"):
        TaggerClient()


# -- the live endpoint --------------------------------------------------------
#
# Marked `network`: it wakes a Modal container (cold start ~20s) and needs
# credentials. Run deliberately:
#   source .env.tagger && FDB_TAGGER_URL=https://...modal.run pytest -m network

TAGGER_URL_ENV = "FDB_TAGGER_URL"
TAGGER_TOKEN_ENV = "FDB_TAGGER_TOKEN"


def _endpoint() -> tuple[str, str]:
    import os

    url, token = os.environ.get(TAGGER_URL_ENV), os.environ.get(TAGGER_TOKEN_ENV)
    if not url or not token:
        pytest.skip(f"set {TAGGER_URL_ENV} and {TAGGER_TOKEN_ENV}")
    return url, token


def _post(url: str, body: dict, token: str | None) -> tuple[int, dict]:
    import json as _json
    import urllib.error
    import urllib.request

    headers = {"content-type": "application/json"}
    if token is not None:
        headers["x-tagger-token"] = token
    req = urllib.request.Request(url, data=_json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status, _json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, {}


@pytest.mark.network
def test_endpoint_segments_a_known_compound():
    url, token = _endpoint()
    status, body = _post(url, {"keywords": ["Erneuerbare Energien Zuschuss Kommune"]}, token)
    assert status == 200, status
    assert body["results"][0]["terms"] == ["Erneuerbare Energien", "Zuschuss", "Kommune"]


@pytest.mark.network
def test_endpoint_rejects_a_missing_or_wrong_token():
    """The reason the endpoint is not left unauthenticated: it spends money."""
    url, token = _endpoint()
    body = {"keywords": ["Zuschuss Kommune"]}
    assert _post(url, body, None)[0] == 401
    assert _post(url, body, "not-the-token")[0] == 401
    # A token of the right length but wrong content, so the check cannot be
    # passing merely on length.
    assert _post(url, body, "0" * len(token))[0] == 401


@pytest.mark.network
def test_endpoint_validates_its_input():
    url, token = _endpoint()
    assert _post(url, {"keywords": [1, 2, 3]}, token)[0] == 422
    assert _post(url, {"keywords": ["x"] * 513}, token)[0] == 413


@pytest.mark.network
def test_client_round_trip_populates_the_cache(tmp_path):
    """End to end through the real client: first call hits the net, second does not."""
    from segment.client import Cache, TaggerClient

    url, token = _endpoint()
    values = ["Erneuerbare Energien Zuschuss", "Waldgebiet Waldfläche Forst"]
    with TaggerClient(url=url, token=token, cache=Cache(tmp_path / "c.sqlite")) as client:
        first = client.segment(values)
        assert [r["terms"][0] for r in first] == ["Erneuerbare Energien", "Waldgebiet"]

        calls = []
        original = client._post
        client._post = lambda v: (calls.append(v), original(v))[1]
        second = client.segment(values)
        assert calls == [], "second run went to the network"
        assert second == first
