"""Unit tests for the benchmark question-bank dialect + RAGAS metric eligibility.

Covers the `retrieval-benchmarking` schema changes of the openspec change
`align-ragas-benchmark-dialect`:
- normalize legacy `question/answer/contexts` banks onto ragas 0.3.5's modern
  `user_input/reference/retrieved_contexts` schema (answer -> reference, NEVER
  -> response), preserving archi extension fields;
- schema validation is per-mode and SEPARATE from metric eligibility (an empty
  reference is valid input);
- per-metric row eligibility: the context metrics skip empty-`reference` rows
  while the answer metrics still score them, each metric means over its own
  eligible subset with a reported scored denominator, and scores attach back by
  per-question key (never positionally).

These helpers are pure and ragas-free so they need neither a config file nor the
benchmark-only ragas dependency (absent from the unit-test env).
"""

from __future__ import annotations

import json
import math

from src.utils.benchmark_schema import (
    bank_status_counts,
    json_safe,
    metric_required_column,
    normalize_bank,
    normalize_record,
    required_fields_for_modes,
    row_is_eligible,
    row_status,
    score_metrics_per_eligibility,
)

# --- normalize_record: legacy -> modern dialect -----------------------------


def test_legacy_record_maps_to_modern_dialect():
    out = normalize_record(
        {"question": "How do I request a GPU?", "answer": "Use --gres=gpu:1."}
    )
    assert out["user_input"] == "How do I request a GPU?"
    assert out["reference"] == "Use --gres=gpu:1."
    # legacy keys are consumed by the rename
    assert "question" not in out
    assert "answer" not in out


def test_answer_maps_to_reference_never_response():
    """The single highest-risk mapping: the bank's ground-truth `answer` is the
    ragas `reference` (ground truth), NOT `response` (the agent's run-time
    answer). This regression pins it so it can never silently revert."""
    out = normalize_record({"question": "q", "answer": "ground truth"})
    assert out["reference"] == "ground truth"
    assert "response" not in out


def test_contexts_maps_to_retrieved_contexts():
    out = normalize_record({"question": "q", "contexts": ["c1", "c2"]})
    assert out["retrieved_contexts"] == ["c1", "c2"]
    assert "contexts" not in out


def test_modern_record_passes_through_unchanged():
    modern = {
        "user_input": "q",
        "reference": "r",
        "retrieved_contexts": ["c"],
        "response": "a",
    }
    assert normalize_record(modern) == modern


def test_extension_fields_are_preserved():
    out = normalize_record(
        {
            "question": "q",
            "answer": "r",
            "sources": ["https://x"],
            "source_match_field": ["url"],
            "anchor_type": "should_refuse",
            "notes": "n",
        }
    )
    assert out["sources"] == ["https://x"]
    assert out["source_match_field"] == ["url"]
    assert out["anchor_type"] == "should_refuse"
    assert out["notes"] == "n"


def test_existing_modern_key_is_not_clobbered_by_legacy():
    # If both dialects are present, the modern value wins and legacy is dropped.
    out = normalize_record({"question": "legacy", "user_input": "modern"})
    assert out["user_input"] == "modern"
    assert "question" not in out


def test_non_dict_record_passes_through():
    assert normalize_record("not-a-dict") == "not-a-dict"


def test_normalize_bank_maps_each_record():
    bank = normalize_bank(
        [{"question": "q1", "answer": "a1"}, {"question": "q2", "answer": "a2"}]
    )
    assert [r["user_input"] for r in bank] == ["q1", "q2"]
    assert [r["reference"] for r in bank] == ["a1", "a2"]


# --- confirmation state: status / source_hashes (maintain-ragas-goldenset) --


def test_status_and_source_hashes_preserved_on_load():
    """The new maintenance fields survive normalization unchanged, so the harness
    loads a bank with them exactly as it loads one without (no scoring change)."""
    out = normalize_record(
        {
            "user_input": "q",
            "reference": "r",
            "status": "locked",
            "source_hashes": {"https://x": "abc123"},
        }
    )
    assert out["status"] == "locked"
    assert out["source_hashes"] == {"https://x": "abc123"}


def test_status_and_source_hashes_survive_normalize_bank():
    bank = normalize_bank(
        [{"user_input": "q", "status": "locked", "source_hashes": {"https://x": "h"}}]
    )
    assert bank[0]["status"] == "locked"
    assert bank[0]["source_hashes"] == {"https://x": "h"}


def test_row_status_absent_is_draft():
    # A row with no `status` is not authoritative -> treated as draft.
    assert row_status({"user_input": "q"}) == "draft"


def test_row_status_locked():
    assert row_status({"user_input": "q", "status": "locked"}) == "locked"


def test_row_status_non_locked_value_is_draft():
    # Conservative: authoritative ONLY when the value is exactly "locked".
    assert row_status({"status": "draft"}) == "draft"
    assert row_status({"status": "whatever"}) == "draft"


def test_bank_status_counts_reports_locked_draft_and_anchor_distribution():
    bank = [
        {"user_input": "a", "status": "locked", "anchor_type": "easy_retrieve"},
        {"user_input": "b", "status": "draft", "anchor_type": "reasoning"},
        {"user_input": "c", "anchor_type": "reasoning"},  # absent status -> draft
        {"user_input": "d", "status": "locked", "anchor_type": "should_refuse"},
    ]
    counts = bank_status_counts(bank)
    assert counts["locked"] == 2
    assert counts["draft"] == 2
    assert counts["total"] == 4
    assert counts["anchor_type"] == {
        "easy_retrieve": 1,
        "reasoning": 2,
        "should_refuse": 1,
    }


def test_bank_status_counts_buckets_missing_anchor_type():
    counts = bank_status_counts([{"user_input": "a", "status": "locked"}])
    assert counts["locked"] == 1
    assert counts["anchor_type"] == {"unassigned": 1}


def test_backfilling_status_is_scoring_neutral():
    """Adding `status` to a row must not change per-metric eligibility (scoring
    ignores `status`), so backfilling the live bank to `draft` is behavior-neutral."""
    base = {"user_input": "q", "reference": "r", "retrieved_contexts": ["c"]}
    with_status = {**base, "status": "draft"}
    for metric in (
        "answer_relevancy",
        "faithfulness",
        "context_precision",
        "context_recall",
    ):
        assert row_is_eligible(base, metric) == row_is_eligible(with_status, metric)


def test_normalize_bank_tolerates_non_dict_items():
    bank = normalize_bank([{"question": "q"}, "junk", 3])
    assert bank[0]["user_input"] == "q"
    assert bank[1] == "junk"
    assert bank[2] == 3


# --- required_fields_for_modes: schema validation, separate from eligibility -


def test_ragas_mode_requires_only_user_input_not_reference():
    # An empty reference is a valid draft row; load must not reject it. Schema
    # validation is separate from per-metric eligibility.
    assert required_fields_for_modes({"modes": ["RAGAS"]}) == ["user_input"]


def test_sources_mode_additionally_requires_sources():
    fields = required_fields_for_modes({"modes": ["SOURCES"]})
    assert "user_input" in fields
    assert "sources" in fields
    assert "reference" not in fields


def test_both_modes_require_user_input_and_sources_only():
    fields = required_fields_for_modes({"modes": ["RAGAS", "SOURCES"]})
    assert set(fields) == {"user_input", "sources"}


def test_modes_are_read_from_the_modes_list_not_top_level_keys():
    # The active modes live in the `modes` list of services.benchmarking, NOT as
    # top-level keys. A stray top-level "SOURCES" key must NOT enable sources
    # enforcement (the pre-existing dead-check bug this fix closes).
    assert required_fields_for_modes({"SOURCES": {}, "modes": ["RAGAS"]}) == [
        "user_input"
    ]


def test_required_fields_tolerates_non_dict_config():
    assert required_fields_for_modes(None) == ["user_input"]


def test_required_fields_tolerates_config_without_modes():
    assert required_fields_for_modes({"provider": "local"}) == ["user_input"]


# --- per-metric row eligibility ---------------------------------------------


def test_context_metrics_require_non_empty_reference():
    row = {
        "user_input": "q",
        "response": "a",
        "reference": "",
        "retrieved_contexts": [],
    }
    assert row_is_eligible(row, "context_precision") is False
    assert row_is_eligible(row, "context_recall") is False


def test_answer_metrics_do_not_require_reference():
    row = {"user_input": "q", "response": "a", "reference": ""}
    assert row_is_eligible(row, "answer_relevancy") is True
    assert row_is_eligible(row, "faithfulness") is True


def test_should_refuse_row_with_referral_reference_is_context_eligible():
    # should_refuse rows carry a NON-EMPTY referral in reference -> eligible for
    # the context metrics (they are a scored refusal case, not a draft row).
    row = {
        "user_input": "q",
        "reference": "See the MIT Engaging team.",
        "response": "a",
    }
    assert row_is_eligible(row, "context_precision") is True


# --- score_metrics_per_eligibility: per-metric subset + keyed attribution ----


def _rows_and_keys():
    rows = [
        {"user_input": "q1", "response": "a1", "reference": "r1"},
        {"user_input": "q2", "response": "a2", "reference": ""},  # draft: no reference
        {"user_input": "q3", "response": "a3", "reference": "r3"},
    ]
    keys = ["question_1", "question_2", "question_3"]
    return rows, keys


def test_context_metric_excludes_draft_row_answer_metric_keeps_it():
    rows, keys = _rows_and_keys()
    qwr = {k: {} for k in keys}
    seen = {}

    def score_fn(metric, eligible_rows):
        seen[metric] = [r["user_input"] for r in eligible_rows]
        return [1.0 for _ in eligible_rows]

    out = score_metrics_per_eligibility(
        rows, keys, ["context_precision", "answer_relevancy"], qwr, score_fn
    )
    # context_precision saw only the two reference-bearing rows
    assert seen["context_precision"] == ["q1", "q3"]
    # answer_relevancy saw all three (reference not required)
    assert seen["answer_relevancy"] == ["q1", "q2", "q3"]
    # the draft row carries the answer metric but NOT the context metric
    assert "context_precision" not in qwr["question_2"]
    assert qwr["question_2"]["answer_relevancy"] == 1.0
    # scored denominators are reported per metric
    assert out["context_precision_scored"] == "2 of 3"
    assert out["answer_relevancy_scored"] == "3 of 3"
    assert out["aggregate_context_precision"] == 1.0


def test_excluded_row_does_not_shift_scores_onto_wrong_question():
    """Codex #93 F5: attach each score back by per-question key, never
    positionally — an excluded row must not slide another row's score."""
    rows, keys = _rows_and_keys()
    qwr = {k: {} for k in keys}

    def score_fn(metric, eligible_rows):
        # distinct scores so a positional misattach would be visible
        return [0.11, 0.33]  # for the two eligible rows q1, q3

    score_metrics_per_eligibility(rows, keys, ["context_recall"], qwr, score_fn)
    assert qwr["question_1"]["context_recall"] == 0.11
    assert qwr["question_3"]["context_recall"] == 0.33
    # question_2 (the excluded draft row) must NOT have picked up q3's 0.33
    assert "context_recall" not in qwr["question_2"]


def test_metric_with_no_eligible_rows_records_na_without_scoring():
    """Codex #93 F6 / task 2.8: a metric whose eligible subset is empty records
    n/a for the whole config instead of invoking RAGAS on an empty dataset."""
    rows = [
        {"user_input": "q1", "reference": ""},
        {"user_input": "q2", "reference": ""},
    ]
    keys = ["question_1", "question_2"]
    qwr = {k: {} for k in keys}
    called = []

    def score_fn(metric, eligible_rows):
        called.append(metric)
        return [1.0 for _ in eligible_rows]

    out = score_metrics_per_eligibility(
        rows, keys, ["context_precision"], qwr, score_fn
    )
    assert called == []  # score_fn never invoked on an empty subset
    assert math.isnan(out["aggregate_context_precision"])
    assert out["context_precision_scored"] == "0 of 2"


def test_aggregate_is_mean_over_eligible_rows():
    rows = [
        {"user_input": "q1", "reference": "r1"},
        {"user_input": "q2", "reference": "r2"},
    ]
    keys = ["question_1", "question_2"]
    qwr = {k: {} for k in keys}

    def score_fn(metric, eligible_rows):
        return [1.0, 0.0]

    out = score_metrics_per_eligibility(
        rows, keys, ["context_precision"], qwr, score_fn
    )
    assert out["aggregate_context_precision"] == 0.5
    assert out["context_precision_scored"] == "2 of 2"


def test_normalize_bank_non_list_passes_through():
    # A malformed bank that is not a list is returned untouched (the caller's own
    # load-path handling reports the error).
    assert normalize_bank({"question": "q"}) == {"question": "q"}


def test_aggregate_is_nan_when_every_eligible_cell_is_nan():
    # ragas can emit NaN for a cell it could not score even on an eligible row;
    # if every eligible cell is NaN the aggregate is NaN (not 0 / a crash).
    rows = [{"user_input": "q1", "reference": "r1"}]
    keys = ["question_1"]
    qwr = {k: {} for k in keys}

    def score_fn(metric, eligible_rows):
        return [math.nan]

    out = score_metrics_per_eligibility(rows, keys, ["context_recall"], qwr, score_fn)
    assert math.isnan(out["aggregate_context_recall"])
    # #279: the denominator counts what reached the aggregate. Nothing did, so
    # this reads "0 of 1" — the same shape an ineligible subset already reports,
    # and NOT "1 of 1", which claimed a scored row behind a NaN mean.
    assert out["context_recall_scored"] == "0 of 1"


def test_scored_count_excludes_nan_cells():
    """#279: ``<metric>_scored`` is the number of values that CONTRIBUTED to the
    aggregate, not the size of the eligible subset.

    ``_mean_ignoring_nan`` already drops the NaN cell, so counting eligible rows
    published a denominator the average never used — the artifact that prompted
    this recorded ``context_precision_scored: "109 of 109"`` over 108 finite
    scores, and §3.4 of the interpreting guide tells readers to trust exactly
    that number as their defence against denominator drift.
    """
    rows = [
        {"user_input": "q1", "reference": "r1"},
        {"user_input": "q2", "reference": "r2"},
        {"user_input": "q3", "reference": "r3"},
    ]
    keys = ["question_1", "question_2", "question_3"]
    qwr = {k: {} for k in keys}

    def score_fn(metric, eligible_rows):
        return [0.5, math.nan, 1.0]

    out = score_metrics_per_eligibility(
        rows, keys, ["context_precision"], qwr, score_fn
    )
    assert out["context_precision_scored"] == "2 of 3"
    assert out["aggregate_context_precision"] == 0.75
    # The per-question cell keeps the NaN: the row WAS handed to the judge, and
    # the artifact must still show which row went unscored.
    assert math.isnan(qwr["question_2"]["context_precision"])


def test_scored_zero_is_not_reported_as_unscored():
    """The counterpart of the NaN case: a genuine 0.0 is a score. It counts
    toward the denominator and keeps the aggregate at 0.0, so "scored zero"
    never collapses into "unscored"."""
    rows = [
        {"user_input": "q1", "reference": "r1"},
        {"user_input": "q2", "reference": "r2"},
    ]
    keys = ["question_1", "question_2"]
    qwr = {k: {} for k in keys}

    def score_fn(metric, eligible_rows):
        return [0.0, 0.0]

    out = score_metrics_per_eligibility(rows, keys, ["context_recall"], qwr, score_fn)
    assert out["context_recall_scored"] == "2 of 2"
    assert out["aggregate_context_recall"] == 0.0


def test_scored_count_excludes_infinities():
    """Non-finite covers more than NaN: an infinite cell is no more a usable
    score than a NaN one, and it is what ``allow_nan=False`` would refuse to
    serialize, so both must leave the numerator the same way."""
    rows = [
        {"user_input": "q1", "reference": "r1"},
        {"user_input": "q2", "reference": "r2"},
    ]
    keys = ["question_1", "question_2"]
    qwr = {k: {} for k in keys}

    def score_fn(metric, eligible_rows):
        return [1.0, math.inf]

    out = score_metrics_per_eligibility(rows, keys, ["context_recall"], qwr, score_fn)
    assert out["context_recall_scored"] == "1 of 2"
    assert out["aggregate_context_recall"] == 1.0


# --- json_safe: the serialization boundary ----------------------------------


def test_json_safe_replaces_non_finite_with_none_and_does_not_mutate():
    """#279: NaN/Infinity are not JSON. ``json_safe`` maps them to ``null`` in a
    COPY — the in-memory results stay NaN because ``pair_ab_results`` and the
    leaderboard both call ``math.isnan`` on them after the dump."""
    original = {
        "benchmarking_results": [
            {
                "total_results": {
                    "aggregate_context_recall": math.nan,
                    "aggregate_faithfulness": 0.0,
                },
                "single_question_results": {
                    "question_1": {"context_recall": math.nan, "faithfulness": 0.0},
                },
                "notes": [1.0, math.inf, -math.inf, "ok", None],
            }
        ],
        "metadata": {"time": "2026-09-03"},
    }

    safe = json_safe(original)

    arm = safe["benchmarking_results"][0]
    assert arm["total_results"]["aggregate_context_recall"] is None
    assert arm["single_question_results"]["question_1"]["context_recall"] is None
    assert arm["notes"] == [1.0, None, None, "ok", None]
    # a scored zero survives as a number, never as null
    assert arm["total_results"]["aggregate_faithfulness"] == 0.0
    assert arm["single_question_results"]["question_1"]["faithfulness"] == 0.0
    # strings, ints and metadata pass through untouched
    assert safe["metadata"] == {"time": "2026-09-03"}

    # the source is untouched, and every container is a fresh object
    src_arm = original["benchmarking_results"][0]
    assert math.isnan(src_arm["total_results"]["aggregate_context_recall"])
    assert math.isnan(
        src_arm["single_question_results"]["question_1"]["context_recall"]
    )
    assert math.isinf(src_arm["notes"][1])
    assert safe is not original
    assert safe["benchmarking_results"] is not original["benchmarking_results"]
    assert src_arm["total_results"] is not arm["total_results"]


def test_json_safe_output_serializes_with_allow_nan_false():
    """The contract the writer depends on: whatever ``json_safe`` returns is
    accepted by a strict serializer, so the harness can turn ``allow_nan`` off
    and have an invalid artifact become impossible rather than merely unlikely."""
    payload = {"a": math.nan, "b": [math.inf], "c": {"d": -math.inf}, "e": 0.0}

    text = json.dumps(json_safe(payload), allow_nan=False)

    assert json.loads(text) == {"a": None, "b": [None], "c": {"d": None}, "e": 0.0}


def test_json_safe_normalizes_tuples_to_lists():
    """``json.dump`` writes a tuple as an array anyway; the copy makes that
    explicit so the returned structure is exactly what lands on disk."""
    assert json_safe({"t": (1.0, math.nan)}) == {"t": [1.0, None]}


def test_json_safe_leaves_bools_alone():
    """``bool`` is a subclass of ``int``: a finite-number check written without
    care would rewrite ``True`` into ``1`` and silently change the schema."""
    out = json_safe({"matched": True, "missing": False})
    assert out["matched"] is True
    assert out["missing"] is False


# --- answer_correctness eligibility (direct answer-vs-reference metric) ------


def test_answer_correctness_requires_a_reference():
    """``answer_correctness`` grades the answer AGAINST the reference, so a row
    with no reference has nothing to grade against and must stay out of the
    denominator — the same eligibility rule the two context metrics follow.

    Guards a silent-wrong-score bug rather than a cosmetic gap:
    ``metric_required_column`` reads the map with ``.get(metric)``, so an
    UNREGISTERED metric reads back as ``None`` (no requirement) and every
    reference-less row would be scored anyway.
    """
    assert metric_required_column("answer_correctness") == "reference"


def test_answer_correctness_excludes_reference_less_rows():
    scored = {"user_input": "q", "response": "a", "reference": "r"}
    draft = {"user_input": "q", "response": "a", "reference": ""}

    assert row_is_eligible(scored, "answer_correctness") is True
    assert row_is_eligible(draft, "answer_correctness") is False
    # The answer-only metrics are unaffected by a missing reference.
    assert row_is_eligible(draft, "answer_relevancy") is True
