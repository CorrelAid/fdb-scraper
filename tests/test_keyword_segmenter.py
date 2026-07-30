"""The parts of the segmenter that hold without a GPU.

Everything here is the contract the model is judged against, so it is worth more
than the model call itself: if the partition check or the metric is wrong, the
number the service reports is meaningless.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SERVICE = Path(__file__).parents[1] / "services" / "keyword_segmenter"
sys.path.insert(0, str(SERVICE))

from segment.metric import pool, score_one  # noqa: E402
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
    counts = {"train": 0, "test": 0}
    for row in labels():
        counts[row["split"]] += 1
    assert counts == {"train": 70, "test": 50}


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
# Everything reported rests on 120 rows labelled by one person, so the labels get
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
    """A join whose first word is an uninflected adjective is a labelling error."""
    for row in labels():
        for lo, hi in spans(row["group_sizes"]):
            first = row["tokens"][lo]
            if hi - lo > 1 and first[0].islower() and _adjective_initial(first):
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

    34 of 51 joins follow from inflection; the rest are proper names, English
    terms, numbers and shared-prefix ellipses, which no orthographic rule covers.
    If that ratio moves a lot, the labels have drifted toward taste.
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
    assert by_rule + judgement == 51
    assert by_rule >= 2 * judgement, f"only {by_rule} of {by_rule + judgement} joins rule-explained"


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
    # Measured 0.965 at seed 0; the floor allows for retraining drift.
    assert scored.f1 > 0.93, f"tagger F1 {scored.f1:.3f} vs baseline {baseline.f1:.3f}"


@pytest.mark.model
def test_single_token_values_never_reach_the_model(tagger):
    assert tagger("Zuschuss") == {"terms": ["Zuschuss"], "group_sizes": [1]}


# -- the cache ----------------------------------------------------------------
#
# No weights and no network needed: a stub stands in for the endpoint, so the
# read-through logic is tested on every run rather than only when the service is up.


class _StubEndpoint:
    """Counts calls, so a test can assert the cache actually prevented them."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, values):
        self.calls.append(list(values))
        return {
            "model": "stub",
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
