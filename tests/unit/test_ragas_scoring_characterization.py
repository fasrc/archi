"""Characterization test: pins the observable side effects of Benchmarker.get_ragas_results.

Must pass unchanged before and after the ragas_scoring extraction (task 1.2 / PR 1).
Never import src.utils.ragas_scoring here.
"""

from __future__ import annotations

import math
import sys
import types

import pandas as pd
import pytest

from src.bin.service_benchmark import Benchmarker, ragas_judge_effective_identity


def _install_ragas_stub(monkeypatch, evaluate_fn):
    """Install a ragas sys.modules stub; calls to evaluate go through evaluate_fn."""
    monkeypatch.setitem(
        sys.modules,
        "ragas",
        types.SimpleNamespace(
            EvaluationDataset=types.SimpleNamespace(from_list=lambda rows: rows),
            RunConfig=lambda **kw: types.SimpleNamespace(**kw),
            evaluate=evaluate_fn,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "ragas.embeddings",
        types.SimpleNamespace(LangchainEmbeddingsWrapper=lambda x: x),
    )
    monkeypatch.setitem(
        sys.modules,
        "ragas.llms",
        types.SimpleNamespace(LangchainLLMWrapper=lambda x: x),
    )
    monkeypatch.setitem(
        sys.modules,
        "ragas.metrics",
        types.SimpleNamespace(
            answer_relevancy=types.SimpleNamespace(name="answer_relevancy"),
            faithfulness=types.SimpleNamespace(name="faithfulness"),
            context_precision=types.SimpleNamespace(name="context_precision"),
            context_recall=types.SimpleNamespace(name="context_recall"),
            answer_correctness=types.SimpleNamespace(name="answer_correctness"),
        ),
    )


def _make_bench(enabled_metrics):
    ragas_settings = {
        "enabled_metrics": enabled_metrics,
        "timeout": 120,
        "batch_size": None,
        "embedding_model": "openai",
    }
    bench = object.__new__(Benchmarker)
    bench.benchmarking_configs = {"mode_settings": {"ragas_settings": ragas_settings}}
    bench.config = {
        "services": {
            "benchmarking": {
                "provider": "openai",
                "model": "gpt-4o",
                "mode_settings": {"ragas_settings": ragas_settings},
            }
        },
        "global": {"verbosity": 0},
    }
    return bench


# ---------------------------------------------------------------------------
# Case 1: two metrics (one answer, one context), three rows, one draft
# ---------------------------------------------------------------------------

_CASE1_SCORES = {
    "answer_relevancy": [0.9, 0.8, 0.7],
    "context_recall": [0.6, 0.5],
}


def test_get_ragas_results_two_metrics_one_draft_pins_aggregates_and_side_effects(
    monkeypatch,
):
    """D5 case 1: answer_relevancy + context_recall, 3 rows (1 draft).

    Asserts exact aggregates, per-row scores, _ragas_scored, _judge_usage,
    _ragas_judge_identity, and the evaluate kwargs (batch_size, run_config,
    callbacks).
    """
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    from src.utils.llm_usage import UsageRecorder

    evaluate_calls = []

    def _evaluate(dataset, metrics=None, **kwargs):
        metric_name = metrics[0].name
        evaluate_calls.append({"metric": metric_name, "kwargs": kwargs})
        msg = AIMessage(
            content="ok",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        llm_result = LLMResult(generations=[[ChatGeneration(message=msg)]])
        for cb in kwargs.get("callbacks", []):
            if hasattr(cb, "on_llm_end"):
                cb.on_llm_end(llm_result)
        scores = _CASE1_SCORES[metric_name]
        return types.SimpleNamespace(
            to_pandas=lambda _s=scores, _n=metric_name: pd.DataFrame({_n: _s})
        )

    _install_ragas_stub(monkeypatch, _evaluate)
    monkeypatch.setattr("src.bin.service_benchmark.ChatOpenAI", lambda **kw: object())
    monkeypatch.setattr("src.bin.service_benchmark.OpenAIEmbeddings", lambda: object())

    bench = _make_bench(["answer_relevancy", "context_recall"])
    rows = [
        {
            "user_input": "q1",
            "retrieved_contexts": ["c1"],
            "response": "a1",
            "reference": "r1",
        },
        {
            "user_input": "q2",
            "retrieved_contexts": ["c2"],
            "response": "a2",
            "reference": "r2",
        },
        {
            "user_input": "q3",
            "retrieved_contexts": ["c3"],
            "response": "a3",
            "reference": "",
        },
    ]
    keys = ["question_1", "question_2", "question_3"]
    results_by_key = {"question_1": {}, "question_2": {}, "question_3": {}}

    aggregates = bench.get_ragas_results(rows, keys, results_by_key)

    # ── aggregates ────────────────────────────────────────────────────────────
    assert aggregates["aggregate_answer_relevancy"] == pytest.approx(0.8)
    assert aggregates["answer_relevancy_scored"] == "3 of 3"
    assert aggregates["aggregate_context_recall"] == pytest.approx(0.55)
    assert aggregates["context_recall_scored"] == "2 of 3"

    # ── per-row scores on results_by_key ─────────────────────────────────────
    assert results_by_key["question_1"]["answer_relevancy"] == pytest.approx(0.9)
    assert results_by_key["question_2"]["answer_relevancy"] == pytest.approx(0.8)
    assert results_by_key["question_3"]["answer_relevancy"] == pytest.approx(0.7)
    assert results_by_key["question_1"]["context_recall"] == pytest.approx(0.6)
    assert results_by_key["question_2"]["context_recall"] == pytest.approx(0.5)
    assert "context_recall" not in results_by_key["question_3"]

    # ── _ragas_scored ─────────────────────────────────────────────────────────
    assert bench._ragas_scored is True

    # ── _judge_usage ─────────────────────────────────────────────────────────
    usage = bench._judge_usage
    assert usage is not None
    assert usage["calls"] == 2
    assert usage["input_tokens"] == 20
    assert usage["output_tokens"] == 10
    assert usage["unreported_calls"] == 0
    assert len(usage["by_model"]) == 1
    assert usage["by_model"][0] == {
        "provider": "openai",
        "model": "gpt-4o",
        "input_tokens": 20,
        "output_tokens": 10,
        "calls": 2,
        "unreported_calls": 0,
    }

    # ── _ragas_judge_identity equals ragas_judge_effective_identity ───────────
    expected_identity = ragas_judge_effective_identity(bench.config)
    assert bench._ragas_judge_identity == expected_identity
    assert expected_identity == {
        "provider": "openai",
        "model": "gpt-4o",
        "base_url": None,
        "mode": None,
    }

    # ── evaluate received the right batch_size, run_config, callbacks ─────────
    assert len(evaluate_calls) == 2
    for call in evaluate_calls:
        kw = call["kwargs"]
        assert kw["batch_size"] is None
        rc = kw["run_config"]
        assert rc.timeout == 120
        assert rc.max_workers == 16
        assert rc.log_tenacity is False
        cbs = kw["callbacks"]
        assert len(cbs) == 1
        assert isinstance(cbs[0], UsageRecorder)


# ---------------------------------------------------------------------------
# Case 2: only a context metric, every row is a draft
# ---------------------------------------------------------------------------


def test_get_ragas_results_context_metric_all_drafts_pins_noscoring_side_effects(
    monkeypatch,
):
    """D5 case 2: context_recall only, all rows are drafts.

    Asserts n/a aggregate, _ragas_scored False, _judge_usage None,
    evaluate never called, _ragas_judge_identity set (D3: judge built eagerly).
    """
    evaluate_calls = []

    def _evaluate(dataset, metrics=None, **kwargs):
        evaluate_calls.append(True)
        return types.SimpleNamespace(to_pandas=lambda: pd.DataFrame({}))

    _install_ragas_stub(monkeypatch, _evaluate)
    monkeypatch.setattr("src.bin.service_benchmark.ChatOpenAI", lambda **kw: object())
    monkeypatch.setattr("src.bin.service_benchmark.OpenAIEmbeddings", lambda: object())

    bench = _make_bench(["context_recall"])
    rows = [
        {
            "user_input": "q1",
            "retrieved_contexts": ["c1"],
            "response": "a1",
            "reference": "",
        },
        {
            "user_input": "q2",
            "retrieved_contexts": ["c2"],
            "response": "a2",
            "reference": "",
        },
        {
            "user_input": "q3",
            "retrieved_contexts": ["c3"],
            "response": "a3",
            "reference": "",
        },
    ]
    keys = ["question_1", "question_2", "question_3"]
    results_by_key = {"question_1": {}, "question_2": {}, "question_3": {}}

    aggregates = bench.get_ragas_results(rows, keys, results_by_key)

    # ── n/a aggregate ─────────────────────────────────────────────────────────
    assert math.isnan(aggregates["aggregate_context_recall"])
    assert aggregates["context_recall_scored"] == "0 of 3"

    # ── evaluate was never called ─────────────────────────────────────────────
    assert evaluate_calls == []

    # ── no scoring took place ─────────────────────────────────────────────────
    assert bench._ragas_scored is False
    assert bench._judge_usage is None

    # ── _ragas_judge_identity is set (D3: judge is built before eligibility) ──
    expected_identity = ragas_judge_effective_identity(bench.config)
    assert bench._ragas_judge_identity == expected_identity
    assert expected_identity == {
        "provider": "openai",
        "model": "gpt-4o",
        "base_url": None,
        "mode": None,
    }
