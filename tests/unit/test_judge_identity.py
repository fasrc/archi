"""The shared judge-identity comparison (#732, design D10, task 3.6a).

``ragas_judge_identity_effective`` grows nested values (the metric list, the
embedding, the scorer identity), so every reader compares identities through one
canonical JSON form with numbers normalized.
"""

from __future__ import annotations

import json

import pytest

from src.bin.service_benchmark import ResultHandler
from src.utils.judge_identity import (
    canonical_identity,
    differing_fields,
    identity_digest,
    normalize_numbers,
)

_FOUR_FIELDS = {
    "provider": "huit_bedrock",
    "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "base_url": "https://go.apis.huit.harvard.edu/ais-bedrock-llm/v2",
    "mode": None,
}


def _nested(**overrides):
    identity = {
        "provider": "vllm",
        "model": "meta-llama/Llama-3.3-70B-Instruct",
        "base_url": None,
        "mode": "unix_socket",
        "model_revision": "6f6073b423013f6a7d4d9f39144961bfbfbc386b",
        "precision": "bf16",
        "decoding": {"temperature": 0, "top_p": None, "max_tokens": None},
        "metrics": ["answer_relevancy", "faithfulness"],
        "embedding": {"class": "OpenAIEmbeddings", "model": "text-embedding-ada-002"},
    }
    identity.update(overrides)
    return identity


def _record(identity):
    return {
        "single_question_results": {},
        "total_results": {"aggregate_faithfulness": 0.5},
        "configuration": {"services": {"benchmarking": {"name": "arm"}}},
        "ragas_effective_settings": {"timeout": 180, "max_workers": 16},
        "ragas_judge_identity_effective": identity,
    }


@pytest.fixture(autouse=True)
def _reset_result_handler():
    saved = (ResultHandler.results, ResultHandler.leaderboard)
    ResultHandler.results = []
    ResultHandler.leaderboard = {}
    yield
    ResultHandler.results, ResultHandler.leaderboard = saved


def test_an_integral_float_equals_the_integer():
    assert canonical_identity({"temperature": 0}) == canonical_identity(
        {"temperature": 0.0}
    )
    assert normalize_numbers({"a": [1.0, 2.5, {"b": 3.0}]}) == {"a": [1, 2.5, {"b": 3}]}


def test_a_bool_is_not_a_number():
    assert canonical_identity({"x": True}) != canonical_identity({"x": 1})
    assert normalize_numbers(True) is True


def test_keys_are_sorted_at_every_depth():
    left = {"b": 1, "a": {"y": 2, "x": 1}}
    right = {"a": {"x": 1, "y": 2}, "b": 1}
    assert canonical_identity(left) == canonical_identity(right)


def test_a_four_field_identity_keeps_its_old_canonical_form():
    """The leaderboard's shared context showed json.dumps(sort_keys=True) before
    this change; a #618 identity (no numbers) must read the same after it."""
    assert canonical_identity(_FOUR_FIELDS) == json.dumps(_FOUR_FIELDS, sort_keys=True)


def test_the_digest_follows_the_canonical_form():
    assert identity_digest({"t": 0}) == identity_digest({"t": 0.0})
    assert identity_digest({"t": 0}).startswith("sha256:")
    assert identity_digest({"t": 0}) != identity_digest({"t": 1})


def test_differing_fields_names_only_the_fields_that_differ():
    assert differing_fields([_nested(), _nested(precision="fp8")]) == ["precision"]
    assert differing_fields([_nested(), _nested()]) == []


def test_a_field_missing_from_one_identity_differs():
    extended = dict(_FOUR_FIELDS, precision="bf16")
    assert differing_fields([_FOUR_FIELDS, extended]) == ["precision"]
    assert differing_fields([_FOUR_FIELDS, dict(_FOUR_FIELDS, precision=None)]) == [
        "precision"
    ]


def test_a_nested_difference_names_its_top_level_field():
    changed = _nested(embedding={"class": "HuggingFaceEmbeddings", "model": "m"})
    assert differing_fields([_nested(), changed]) == ["embedding"]


def test_nested_identities_go_through_the_in_run_check():
    records = [_record(_nested()), _record(_nested())]
    assert ResultHandler.arms_incomparability_reason(records) is None


def test_nested_identities_that_differ_withhold_the_ranks():
    records = [_record(_nested()), _record(_nested(metrics=["faithfulness"]))]
    reason = ResultHandler.arms_incomparability_reason(records)
    assert "different RAGAS judges" in reason
    assert "metrics" in reason


def test_a_precision_only_difference_names_the_precision_field():
    records = [_record(_nested()), _record(_nested(precision="fp8"))]
    reason = ResultHandler.arms_incomparability_reason(records)
    assert "precision" in reason
    assert "client mode" not in reason


def test_temperature_zero_and_zero_point_zero_compare_equal():
    records = [
        _record(_nested(decoding={"temperature": 0})),
        _record(_nested(decoding={"temperature": 0.0})),
    ]
    assert ResultHandler.arms_incomparability_reason(records) is None


def test_four_field_identities_still_compare_as_before():
    same = [_record(dict(_FOUR_FIELDS)), _record(dict(_FOUR_FIELDS))]
    assert ResultHandler.arms_incomparability_reason(same) is None
    other = [_record(dict(_FOUR_FIELDS)), _record(dict(_FOUR_FIELDS, mode="ollama"))]
    reason = ResultHandler.arms_incomparability_reason(other)
    assert "different RAGAS judges" in reason
    assert "mode" in reason


def test_the_leaderboard_reads_nested_identities_without_error():
    ResultHandler.results = [_record(_nested()), _record(_nested())]
    board = ResultHandler.build_leaderboard()
    assert board["comparable"] is True
    assert board["shared_context"]["judge_identity"] == canonical_identity(_nested())


def test_the_leaderboard_sees_no_drift_between_zero_and_zero_point_zero():
    ResultHandler.results = [
        _record(_nested(decoding={"temperature": 0})),
        _record(_nested(decoding={"temperature": 0.0})),
    ]
    board = ResultHandler.build_leaderboard()
    assert not any("judge_identity" in w for w in board["shared_context"]["warnings"])
