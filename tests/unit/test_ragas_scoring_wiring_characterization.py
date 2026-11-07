"""Characterization test: pins what Benchmarker.get_ragas_results hands to ragas.

A sibling of test_ragas_scoring_characterization.py, which stays unchanged since the
commit that added it (design D5). This file adds what that one leaves open: which judge
and embeddings reach ``evaluate``, that non-default settings reach ``evaluate`` and
``RunConfig``, the complete aggregate mapping, and the eager embeddings build when no
row is eligible. It passes on the code before the ragas_scoring move and after it.
Never import src.utils.ragas_scoring here.
"""

from __future__ import annotations

import math
import sys
import types

import pandas as pd
import pytest

from src.bin.service_benchmark import Benchmarker


def _install_ragas_stub(monkeypatch, evaluate_fn):
    """Install a ragas sys.modules stub whose wrappers tag what they wrap."""
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
        types.SimpleNamespace(LangchainEmbeddingsWrapper=lambda x: ("embeddings", x)),
    )
    monkeypatch.setitem(
        sys.modules,
        "ragas.llms",
        types.SimpleNamespace(LangchainLLMWrapper=lambda x: ("llm", x)),
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


def _install_constructors(monkeypatch):
    """Patch the judge and embeddings constructors; return their sentinels and calls."""
    built = {"judge": object(), "embeddings": object(), "calls": []}

    def chat_openai(**kwargs):
        built["calls"].append(("judge", kwargs))
        return built["judge"]

    def openai_embeddings():
        built["calls"].append(("embeddings", {}))
        return built["embeddings"]

    monkeypatch.setattr("src.bin.service_benchmark.ChatOpenAI", chat_openai)
    monkeypatch.setattr("src.bin.service_benchmark.OpenAIEmbeddings", openai_embeddings)
    return built


def _make_bench(enabled_metrics):
    """A bench whose ragas settings and verbosity all differ from the defaults."""
    ragas_settings = {
        "enabled_metrics": enabled_metrics,
        "timeout": 45,
        "batch_size": 7,
        "max_workers": 3,
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
        "global": {"verbosity": 4},
    }
    return bench


def _row(n, reference):
    return {
        "user_input": f"q{n}",
        "retrieved_contexts": [f"c{n}"],
        "response": f"a{n}",
        "reference": reference,
    }


_SCORES = {
    "answer_relevancy": [0.9, 0.8, 0.7],
    "context_recall": [0.6, 0.5],
}


def test_get_ragas_results_hands_the_built_judge_and_settings_to_evaluate(
    monkeypatch,
):
    evaluate_calls = []

    def _evaluate(dataset, metrics=None, **kwargs):
        name = metrics[0].name
        evaluate_calls.append(kwargs)
        return types.SimpleNamespace(
            to_pandas=lambda _s=_SCORES[name], _n=name: pd.DataFrame({_n: _s})
        )

    _install_ragas_stub(monkeypatch, _evaluate)
    built = _install_constructors(monkeypatch)
    bench = _make_bench(["answer_relevancy", "context_recall"])
    keys = ["question_1", "question_2", "question_3"]

    aggregates = bench.get_ragas_results(
        [_row(1, "r1"), _row(2, "r2"), _row(3, "")], keys, {k: {} for k in keys}
    )

    assert set(aggregates) == {
        "aggregate_answer_relevancy",
        "answer_relevancy_scored",
        "aggregate_context_recall",
        "context_recall_scored",
    }
    assert aggregates["aggregate_answer_relevancy"] == pytest.approx(0.8)
    assert aggregates["answer_relevancy_scored"] == "3 of 3"
    assert aggregates["aggregate_context_recall"] == pytest.approx(0.55)
    assert aggregates["context_recall_scored"] == "2 of 3"

    assert built["calls"] == [("judge", {"model": "gpt-4o"}), ("embeddings", {})]
    assert len(evaluate_calls) == 2
    for kwargs in evaluate_calls:
        assert set(kwargs) == {
            "llm",
            "embeddings",
            "run_config",
            "batch_size",
            "callbacks",
        }
        assert kwargs["llm"] == ("llm", built["judge"])
        assert kwargs["embeddings"] == ("embeddings", built["embeddings"])
        assert kwargs["batch_size"] == 7
        assert vars(kwargs["run_config"]) == {
            "timeout": 45,
            "max_workers": 3,
            "log_tenacity": True,
        }


def test_get_ragas_results_builds_the_embeddings_when_no_row_is_eligible(
    monkeypatch,
):
    evaluate_calls = []

    def _evaluate(dataset, metrics=None, **kwargs):
        evaluate_calls.append(kwargs)
        return types.SimpleNamespace(to_pandas=lambda: pd.DataFrame({}))

    _install_ragas_stub(monkeypatch, _evaluate)
    built = _install_constructors(monkeypatch)
    bench = _make_bench(["context_recall"])
    keys = ["question_1", "question_2"]

    aggregates = bench.get_ragas_results(
        [_row(1, ""), _row(2, "")], keys, {k: {} for k in keys}
    )

    assert set(aggregates) == {"aggregate_context_recall", "context_recall_scored"}
    assert math.isnan(aggregates["aggregate_context_recall"])
    assert aggregates["context_recall_scored"] == "0 of 2"
    assert evaluate_calls == []
    assert built["calls"] == [("judge", {"model": "gpt-4o"}), ("embeddings", {})]
