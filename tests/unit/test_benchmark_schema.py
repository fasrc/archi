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

import math

from src.utils.benchmark_schema import (
    normalize_bank,
    normalize_record,
    required_fields_for_modes,
    row_is_eligible,
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
    assert out["context_recall_scored"] == "1 of 1"
