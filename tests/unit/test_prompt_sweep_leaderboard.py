"""Unit tests for the RAGAS prompt-sweep leaderboard aggregator.

Covers ResultHandler.build_leaderboard: one row per config, ranking by a
configurable primary metric, tie handling, incomplete-variant handling
(missing/NaN metrics sort last), name fallback to the prompt stem, and the
shared-context drift check. The aggregator reads per-config aggregates only —
these tests never construct any pairwise A/B data.
"""

from typing import Optional

import pytest

from src.bin.service_benchmark import ResultHandler


def _make_record(
    name,
    agent_md_file,
    *,
    answer_relevancy: Optional[float] = 0.8,
    faithfulness: Optional[float] = 0.8,
    context_precision: Optional[float] = 0.8,
    context_recall: Optional[float] = 0.8,
    model="Qwen/Qwen3.5-35B-A3B-GPTQ-Int4",
    provider="openai",
    evaluator_model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    queries_path="config/benchmarking/queries.json",
    n_questions=3,
    include_name=True,
):
    """Build a ResultHandler.results record shaped like handle_results writes."""
    total_results = {}
    for key, value in (
        ("aggregate_answer_relevancy", answer_relevancy),
        ("aggregate_faithfulness", faithfulness),
        ("aggregate_context_precision", context_precision),
        ("aggregate_context_recall", context_recall),
    ):
        if value is not None:  # omit the key entirely to model a missing metric
            total_results[key] = value

    benchmarking = {
        "agent_md_file": agent_md_file,
        "model": model,
        "provider": provider,
        "queries_path": queries_path,
        "mode_settings": {"ragas_settings": {"evaluator_model": evaluator_model}},
    }
    if include_name:
        benchmarking["name"] = name

    return {
        "single_question_results": {f"q{i}": {} for i in range(n_questions)},
        "total_results": total_results,
        "configuration_file": f"/tmp/{name}.yaml",
        "configuration": {"services": {"benchmarking": benchmarking}},
    }


@pytest.fixture(autouse=True)
def _reset_result_handler():
    """ResultHandler holds class-level mutable state; reset around every test."""
    saved_results = ResultHandler.results
    saved_leaderboard = ResultHandler.leaderboard
    ResultHandler.results = []
    ResultHandler.leaderboard = {}
    yield
    ResultHandler.results = saved_results
    ResultHandler.leaderboard = saved_leaderboard


# -- 4.2 one row per config --------------------------------------------------


def test_one_row_per_config():
    ResultHandler.results = [
        _make_record(
            "v1-strict", "config/agents/fasrc-cannon-v1-strict.md", faithfulness=0.7
        ),
        _make_record(
            "v2-lean", "config/agents/fasrc-cannon-v2-lean.md", faithfulness=0.9
        ),
        _make_record(
            "v3-cited", "config/agents/fasrc-cannon-v3-cited.md", faithfulness=0.8
        ),
    ]
    lb = ResultHandler.build_leaderboard()
    assert len(lb["rows"]) == 3
    by_name = {r["name"]: r for r in lb["rows"]}
    assert set(by_name) == {"v1-strict", "v2-lean", "v3-cited"}
    row = by_name["v2-lean"]
    assert row["agent_md_file"] == "config/agents/fasrc-cannon-v2-lean.md"
    assert set(row["metrics"]) == {
        "answer_relevancy",
        "faithfulness",
        "context_precision",
        "context_recall",
    }
    assert row["metrics"]["faithfulness"] == pytest.approx(0.9)


# -- 4.3 default + configured ranking ----------------------------------------


def test_default_ranking_by_faithfulness():
    ResultHandler.results = [
        _make_record("low", "p/low.md", faithfulness=0.5),
        _make_record("high", "p/high.md", faithfulness=0.95),
        _make_record("mid", "p/mid.md", faithfulness=0.7),
    ]
    lb = ResultHandler.build_leaderboard()
    assert lb["primary_metric"] == "faithfulness"
    assert lb["rows"][0]["name"] == "high"
    assert lb["rows"][0]["rank"] == 1
    assert [r["name"] for r in lb["rows"]] == ["high", "mid", "low"]


def test_configured_primary_metric_reranks():
    ResultHandler.results = [
        _make_record("a", "p/a.md", faithfulness=0.9, answer_relevancy=0.2),
        _make_record("b", "p/b.md", faithfulness=0.4, answer_relevancy=0.99),
    ]
    lb = ResultHandler.build_leaderboard("answer_relevancy")
    assert lb["primary_metric"] == "answer_relevancy"
    assert lb["rows"][0]["name"] == "b"
    # all four metric values still present regardless of primary
    assert set(lb["rows"][0]["metrics"]) == {
        "answer_relevancy",
        "faithfulness",
        "context_precision",
        "context_recall",
    }


def test_unknown_primary_metric_falls_back_to_faithfulness():
    ResultHandler.results = [
        _make_record("a", "p/a.md", faithfulness=0.6),
        _make_record("b", "p/b.md", faithfulness=0.8),
    ]
    lb = ResultHandler.build_leaderboard("not_a_metric")
    assert lb["primary_metric"] == "faithfulness"
    assert lb["rows"][0]["name"] == "b"


# -- 4.4 tie handling --------------------------------------------------------


def test_ties_share_a_rank():
    ResultHandler.results = [
        _make_record("a", "p/a.md", faithfulness=0.8),
        _make_record("b", "p/b.md", faithfulness=0.8),
        _make_record("c", "p/c.md", faithfulness=0.5),
    ]
    lb = ResultHandler.build_leaderboard()
    ranks = {r["name"]: r["rank"] for r in lb["rows"]}
    assert ranks["a"] == ranks["b"] == 1
    assert ranks["c"] == 2


# -- 4.5 incomplete handling -------------------------------------------------


def test_missing_metric_marks_incomplete_and_sorts_last():
    ResultHandler.results = [
        _make_record("complete-low", "p/low.md", faithfulness=0.3),
        _make_record("missing", "p/missing.md", faithfulness=None),  # key omitted
    ]
    lb = ResultHandler.build_leaderboard()
    by_name = {r["name"]: r for r in lb["rows"]}
    assert by_name["missing"]["metrics"]["faithfulness"] is None
    assert by_name["missing"]["incomplete"] is True
    # complete row ranks ahead of incomplete even though its score (0.3) is low
    assert lb["rows"][0]["name"] == "complete-low"
    assert lb["rows"][-1]["name"] == "missing"


def test_nan_metric_marks_incomplete():
    rec = _make_record("nan", "p/nan.md")
    rec["total_results"]["aggregate_faithfulness"] = float("nan")
    ResultHandler.results = [
        _make_record("ok", "p/ok.md", faithfulness=0.6),
        rec,
    ]
    lb = ResultHandler.build_leaderboard()
    by_name = {r["name"]: r for r in lb["rows"]}
    assert by_name["nan"]["metrics"]["faithfulness"] is None
    assert by_name["nan"]["incomplete"] is True
    assert lb["rows"][-1]["name"] == "nan"


# -- scored_counts: per-metric sample size behind each mean ------------------


def test_scored_counts_reflect_non_nan_per_question():
    """A judge timeout shrinks a metric's sample without making the aggregate
    NaN; scored_counts must report the real (non-NaN) per-metric count while
    query_count stays the answered count."""
    rec = _make_record(
        "v", "p/v.md", faithfulness=0.6, context_precision=0.5, n_questions=3
    )
    qs = list(rec["single_question_results"].values())
    for q in qs:  # all three fully scored on three metrics
        q["faithfulness"] = 0.6
        q["answer_relevancy"] = 0.8
        q["context_recall"] = 0.7
    # context_precision scored on only the first question; other two timed out
    qs[0]["context_precision"] = 0.5
    qs[1]["context_precision"] = float("nan")
    qs[2]["context_precision"] = float("nan")

    ResultHandler.results = [rec]
    row = ResultHandler.build_leaderboard()["rows"][0]

    assert row["query_count"] == 3  # answered
    assert row["scored_counts"]["faithfulness"] == 3
    assert row["scored_counts"]["answer_relevancy"] == 3
    assert row["scored_counts"]["context_recall"] == 3
    assert row["scored_counts"]["context_precision"] == 1  # 2 timed out
    # the aggregate is still a valid float, so the row is NOT marked incomplete
    assert row["incomplete"] is False


# -- 4.6 name fallback -------------------------------------------------------


def test_name_falls_back_to_prompt_stem():
    ResultHandler.results = [
        _make_record(
            "ignored",
            "config/agents/fasrc-cannon-v4-linked.md",
            faithfulness=0.7,
            include_name=False,
        ),
        _make_record(
            "named", "config/agents/fasrc-cannon-v1-strict.md", faithfulness=0.6
        ),
    ]
    lb = ResultHandler.build_leaderboard()
    names = {r["name"] for r in lb["rows"]}
    assert "fasrc-cannon-v4-linked" in names  # stem, not config_0
    assert not any(n.startswith("config_") for n in names)


# -- 4.7 shared context ------------------------------------------------------


def test_shared_context_uniform_sweep():
    ResultHandler.results = [
        _make_record("a", "p/a.md", faithfulness=0.7),
        _make_record("b", "p/b.md", faithfulness=0.8),
    ]
    lb = ResultHandler.build_leaderboard()
    ctx = lb["shared_context"]
    assert ctx["model"] == "Qwen/Qwen3.5-35B-A3B-GPTQ-Int4"
    assert ctx["provider"] == "openai"
    assert ctx["evaluator_model"] == "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    assert ctx["queries_path"] == "config/benchmarking/queries.json"
    assert ctx["corpus_snapshot_id"]
    assert ctx["warnings"] == []


def test_shared_context_flags_model_drift():
    ResultHandler.results = [
        _make_record("a", "p/a.md", faithfulness=0.7, model="modelA"),
        _make_record("b", "p/b.md", faithfulness=0.8, model="modelB"),
    ]
    lb = ResultHandler.build_leaderboard()
    ctx = lb["shared_context"]
    assert ctx["warnings"], "expected a drift warning"
    assert any("model" in w for w in ctx["warnings"])
    # rows still emitted despite drift
    assert len(lb["rows"]) == 2
