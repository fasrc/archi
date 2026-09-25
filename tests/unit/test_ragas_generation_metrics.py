"""Unit tests for the generation-side RAGAS metrics and the metric registry.

Adds five opt-in metrics aimed at the answer rather than retrieval — the half a
prompt edit can move:

- ``factual_correctness_recall``: share of the reference's claims the answer
  covers (omission).
- ``factual_correctness_precision``: share of the answer's claims the reference
  supports (over-claiming).
- ``noise_sensitivity``: share of the answer's claims that are wrong given the
  retrieved context. LOWER is better — the first such metric, so every reader
  that picks a winner or flags a regression must consult the direction.
- ``answer_accuracy`` / ``response_groundedness``: ragas' dual-judge (averaged)
  variants of correctness and faithfulness, steadier on small banks.

One registry in ``benchmark_schema`` names every metric; the other hand-kept
lists are checked against it here so a future metric cannot land in half of
them. ragas itself is absent from the unit-test env, so metric construction is
exercised against a fake ``ragas.metrics`` module.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from scripts.benchmarking import compare_runs as cr
from scripts.benchmarking import generate_prompt_sweep as gps
from src.bin.service_benchmark import ResultHandler
from src.utils import benchmark_argilla
from src.utils.benchmark_resilience import build_ragas_aggregates
from src.utils.benchmark_schema import (
    LOWER_IS_BETTER_METRICS,
    RAGAS_METRIC_LABELS,
    RAGAS_METRIC_NAMES,
    build_ragas_metric_objects,
    metric_required_column,
    metric_winner,
    ragas_result_column,
)
from src.utils.generate_benchmark_report import RAGAS_METRIC_LABELS as report_labels
from src.utils.generate_benchmark_report import format_markdown_output

NEW_METRICS = (
    "factual_correctness_recall",
    "factual_correctness_precision",
    "noise_sensitivity",
    "answer_accuracy",
    "response_groundedness",
)
LEGACY_METRICS = (
    "answer_relevancy",
    "faithfulness",
    "context_precision",
    "context_recall",
    "answer_correctness",
)


# --- registry ---------------------------------------------------------------


def test_registry_keeps_the_legacy_order_and_appends_the_new_metrics():
    assert RAGAS_METRIC_NAMES == LEGACY_METRICS + NEW_METRICS


def test_every_registered_metric_has_a_display_label():
    assert set(RAGAS_METRIC_LABELS) == set(RAGAS_METRIC_NAMES)
    assert RAGAS_METRIC_LABELS["noise_sensitivity"].endswith("(lower is better)")


@pytest.mark.parametrize(
    "metric, column",
    [
        ("factual_correctness_recall", "reference"),
        ("factual_correctness_precision", "reference"),
        ("noise_sensitivity", "reference"),
        ("answer_accuracy", "reference"),
        ("response_groundedness", None),
    ],
)
def test_new_metrics_declare_the_column_they_need(metric, column):
    """A reference-grading metric must skip draft rows with an empty reference;
    groundedness judges the answer against the contexts only."""
    assert metric_required_column(metric) == column


def test_only_noise_sensitivity_is_lower_is_better():
    assert LOWER_IS_BETTER_METRICS == frozenset({"noise_sensitivity"})


# --- direction-aware winner -------------------------------------------------


@pytest.mark.parametrize(
    "metric, a, b, expected",
    [
        ("faithfulness", 0.9, 0.5, "a"),
        ("faithfulness", 0.5, 0.9, "b"),
        ("noise_sensitivity", 0.1, 0.4, "a"),
        ("noise_sensitivity", 0.4, 0.1, "b"),
        ("noise_sensitivity", 0.2, 0.2, "tie"),
        ("faithfulness", math.nan, 0.9, "tie"),
    ],
)
def test_metric_winner_respects_direction(metric, a, b, expected):
    assert metric_winner(metric, a, b) == expected


# --- ragas result column ----------------------------------------------------


def test_result_column_is_the_name_for_a_plain_metric():
    assert ragas_result_column(SimpleNamespace(name="answer_accuracy")) == (
        "answer_accuracy"
    )


def test_result_column_carries_the_mode_for_a_mode_metric():
    """ragas 0.3.5 evaluation.py keys any object with ``name`` AND ``mode`` as
    ``name(mode=...)`` — reading ``to_pandas()[name]`` would KeyError."""
    metric = SimpleNamespace(name="factual_correctness_recall", mode="recall")
    assert ragas_result_column(metric) == "factual_correctness_recall(mode=recall)"


# --- metric construction ----------------------------------------------------


class _Recorder:
    """Stand-in for a ragas metric class: records its constructor kwargs."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.name = kwargs.get("name")
        if "mode" in kwargs:
            self.mode = kwargs["mode"]


def _fake_ragas_metrics():
    return SimpleNamespace(
        answer_relevancy="AR",
        faithfulness="F",
        context_precision="CP",
        context_recall="CR",
        answer_correctness="AC",
        FactualCorrectness=type("FactualCorrectness", (_Recorder,), {}),
        NoiseSensitivity=type("NoiseSensitivity", (_Recorder,), {}),
        AnswerAccuracy=type("AnswerAccuracy", (_Recorder,), {}),
        ResponseGroundedness=type("ResponseGroundedness", (_Recorder,), {}),
    )


def test_legacy_metrics_use_the_pre_instantiated_objects():
    objs = build_ragas_metric_objects(_fake_ragas_metrics(), LEGACY_METRICS)
    assert objs == {
        "answer_relevancy": "AR",
        "faithfulness": "F",
        "context_precision": "CP",
        "context_recall": "CR",
        "answer_correctness": "AC",
    }


def test_new_metrics_are_built_with_a_fixed_name_and_mode():
    objs = build_ragas_metric_objects(_fake_ragas_metrics(), NEW_METRICS)
    assert objs["factual_correctness_recall"].kwargs == {
        "mode": "recall",
        "name": "factual_correctness_recall",
    }
    assert objs["factual_correctness_precision"].kwargs == {
        "mode": "precision",
        "name": "factual_correctness_precision",
    }
    assert objs["noise_sensitivity"].kwargs == {
        "mode": "relevant",
        "name": "noise_sensitivity",
    }
    assert objs["answer_accuracy"].kwargs == {"name": "answer_accuracy"}
    assert objs["response_groundedness"].kwargs == {"name": "response_groundedness"}
    # Every built metric reads back under a column the harness can predict.
    assert ragas_result_column(objs["factual_correctness_recall"]) == (
        "factual_correctness_recall(mode=recall)"
    )
    assert ragas_result_column(objs["answer_accuracy"]) == "answer_accuracy"


def test_building_an_unknown_metric_is_refused():
    with pytest.raises(KeyError, match="bogus_metric"):
        build_ragas_metric_objects(_fake_ragas_metrics(), ["bogus_metric"])


def test_real_ragas_names_match_the_registry():
    """Where ragas is installed (the benchmark image, the archi conda env),
    confirm the real classes accept the kwargs and name their columns as the
    registry predicts."""
    ragas_metrics = pytest.importorskip("ragas.metrics")
    objs = build_ragas_metric_objects(ragas_metrics, NEW_METRICS)
    assert {name: obj.name for name, obj in objs.items()} == {
        name: name for name in NEW_METRICS
    }
    assert objs["factual_correctness_precision"].mode == "precision"
    assert objs["noise_sensitivity"].mode == "relevant"


# --- hand-kept lists stay in sync with the registry --------------------------


def test_placeholder_aggregates_cover_every_metric():
    keys = set(build_ragas_aggregates(None))
    assert keys == {f"aggregate_{m}" for m in RAGAS_METRIC_NAMES}


def test_leaderboard_knows_every_metric():
    assert [name for name, _ in ResultHandler.LEADERBOARD_METRICS] == list(
        RAGAS_METRIC_NAMES
    )


def test_compare_runs_metrics_and_direction_match_the_registry():
    """compare_runs avoids importing src at module load, so it keeps its own
    copy — this is the tie that keeps the copy honest."""
    assert cr.METRICS == RAGAS_METRIC_NAMES
    assert cr.LOWER_IS_BETTER == LOWER_IS_BETTER_METRICS


def test_prompt_sweep_accepts_every_metric():
    assert gps.KNOWN_METRICS == set(RAGAS_METRIC_NAMES)


def test_report_labels_match_the_registry():
    assert report_labels == RAGAS_METRIC_LABELS
    assert list(report_labels) == list(RAGAS_METRIC_NAMES)


def test_argilla_lists_every_metric():
    assert benchmark_argilla.RAGAS_METRICS == list(RAGAS_METRIC_NAMES)


# --- compare_runs regression direction ---------------------------------------


@pytest.mark.parametrize(
    "metric, delta, expected",
    [
        ("faithfulness", -0.1, 0.1),
        ("faithfulness", 0.1, -0.1),
        ("noise_sensitivity", 0.1, 0.1),
        ("noise_sensitivity", -0.1, -0.1),
    ],
)
def test_worsening_is_positive_when_the_metric_got_worse(metric, delta, expected):
    assert cr.worsening(metric, delta) == pytest.approx(expected)


# --- consumers ---------------------------------------------------------------


def _leaderboard_record(name, noise):
    return {
        "configuration": {
            "name": name,
            "services": {
                "benchmarking": {
                    "agent_md_file": f"{name}.md",
                    "mode_settings": {
                        "ragas_settings": {
                            "enabled_metrics": ["noise_sensitivity"],
                        }
                    },
                }
            },
        },
        "total_results": {"aggregate_noise_sensitivity": noise},
        "single_question_results": {"question_1": {"noise_sensitivity": noise}},
    }


@pytest.fixture
def _reset_results():
    saved = ResultHandler.results
    ResultHandler.results = []
    yield
    ResultHandler.results = saved


def test_leaderboard_ranks_a_lower_is_better_primary_ascending(_reset_results):
    ResultHandler.results = [
        _leaderboard_record("noisy", 0.4),
        _leaderboard_record("clean", 0.1),
    ]
    lb = ResultHandler.build_leaderboard(primary_metric="noise_sensitivity")
    assert lb["primary_metric"] == "noise_sensitivity"
    assert [row["name"] for row in lb["rows"]] == ["clean", "noisy"]


def _ab_row(noise, recall):
    return {
        "question": "How do I submit a job?",
        "status": "ok",
        "answer": "sbatch",
        "noise_sensitivity": noise,
        "factual_correctness_recall": recall,
    }


def test_ab_pairing_picks_the_lower_noise_sensitivity(_reset_results):
    ResultHandler.results = [
        {"single_question_results": {"question_1": _ab_row(0.1, 0.5)}},
        {"single_question_results": {"question_1": _ab_row(0.4, 0.9)}},
    ]
    (paired,) = ResultHandler.pair_ab_results()
    assert paired.winner_by_metric == {
        "factual_correctness_recall": "b",
        "noise_sensitivity": "a",
    }


def test_markdown_report_labels_the_new_per_question_metrics():
    row = {
        "question": "How do I submit a job?",
        "status": "ok",
        "answer": "Use sbatch.",
        "reference_answer": "Submit with sbatch.",
        "factual_correctness_recall": 0.75,
        "noise_sensitivity": 0.25,
    }
    md = format_markdown_output(
        {"services": {"benchmarking": {"modes": ["RAGAS"]}}},
        "ragas-bench",
        "2026-09-25",
        {"question_1": row},
        {"aggregate_noise_sensitivity": 0.25},
        None,
    )
    assert "| Factual Correctness (recall) | " in md
    assert "| Noise Sensitivity (lower is better) | " in md
