"""Harness-level tests for the ragas 0.3.5 modern-dialect scoring path.

Covers the `retrieval-benchmarking` change `align-ragas-benchmark-dialect` at the
`service_benchmark` seam:
- `get_ragas_results` builds a modern-column ragas dataset (user_input/
  retrieved_contexts/response/reference), scores each metric over its OWN
  eligible subset (context metrics skip empty-reference rows), and attaches
  scores back by per-question key;
- `_merge_anchor_questions` normalizes the separately-loaded anchor file onto the
  modern dialect and dedups on `user_input`.

ragas is a benchmark-container-only dep absent from the unit env, so it is stubbed
via `sys.modules` exactly like the existing `datasets` stub in
test_benchmark_resilience.py — no real ragas import.
"""

from __future__ import annotations

import json
import sys
import types

import pandas as pd

from src.bin.service_benchmark import Benchmarker


def _install_ragas_stub(monkeypatch):
    """Stub the ragas import surface get_ragas_results touches; return the list of
    row-batches handed to EvaluationDataset.from_list so the caller can assert the
    modern column shape and per-metric eligibility."""
    captured_from_list = []

    def from_list(rows):
        captured_from_list.append(rows)
        return rows  # dataset stands in for the row list; len() is all we need

    def evaluate(dataset, metrics=None, **kwargs):
        # One metric at a time (the harness scores per-metric); build a frame with
        # a column per metric name and one score per eligible row.
        cols = {m.name: [0.9 for _ in dataset] for m in metrics}
        return types.SimpleNamespace(to_pandas=lambda: pd.DataFrame(cols))

    monkeypatch.setitem(
        sys.modules,
        "ragas",
        types.SimpleNamespace(
            EvaluationDataset=types.SimpleNamespace(from_list=from_list),
            RunConfig=lambda **kw: types.SimpleNamespace(**kw),
            evaluate=evaluate,
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
        ),
    )
    return captured_from_list


def _ragas_bench(enabled_metrics):
    bench = object.__new__(Benchmarker)
    ragas_settings = {
        "enabled_metrics": enabled_metrics,
        "timeout": 60,
        "batch_size": None,
    }
    bench.benchmarking_configs = {"mode_settings": {"ragas_settings": ragas_settings}}
    bench.config = {
        "services": {
            "benchmarking": {"mode_settings": {"ragas_settings": ragas_settings}}
        },
        "global": {"verbosity": 0},
    }
    bench.get_ragas_llm_evaluator = lambda: "llm"
    bench.get_ragas_embedding_model = lambda: "emb"
    return bench


def test_get_ragas_results_builds_modern_dialect_and_scores_per_eligibility(
    monkeypatch,
):
    captured = _install_ragas_stub(monkeypatch)
    bench = _ragas_bench(["answer_relevancy", "context_precision"])

    rows = [
        {
            "user_input": "q1",
            "retrieved_contexts": ["c1"],
            "response": "a1",
            "reference": "r1",
        },
        {  # draft row: empty reference
            "user_input": "q2",
            "retrieved_contexts": ["c2"],
            "response": "a2",
            "reference": "",
        },
    ]
    keys = ["question_1", "question_2"]
    results_by_key = {"question_1": {}, "question_2": {}}

    out = bench.get_ragas_results(rows, keys, results_by_key)

    # Modern columns only, no legacy question/contexts/answer/ground_truth.
    for batch in captured:
        for row in batch:
            assert set(row) >= {
                "user_input",
                "retrieved_contexts",
                "response",
                "reference",
            }
            assert not ({"question", "contexts", "answer", "ground_truth"} & set(row))

    # answer_relevancy scored both rows; context_precision only the row with a
    # non-empty reference (the draft row is excluded from the context metric).
    assert out["answer_relevancy_scored"] == "2 of 2"
    assert out["context_precision_scored"] == "1 of 2"
    assert out["aggregate_answer_relevancy"] == 0.9
    assert out["aggregate_context_precision"] == 0.9

    # Scores attach by key: the draft row carries the answer metric, not context.
    assert results_by_key["question_2"]["answer_relevancy"] == 0.9
    assert "context_precision" not in results_by_key["question_2"]
    assert results_by_key["question_1"]["context_precision"] == 0.9


def test_get_ragas_results_context_metric_all_draft_is_na_without_scoring(monkeypatch):
    captured = _install_ragas_stub(monkeypatch)
    bench = _ragas_bench(["context_recall"])

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
    ]
    keys = ["question_1", "question_2"]
    results_by_key = {"question_1": {}, "question_2": {}}

    out = bench.get_ragas_results(rows, keys, results_by_key)

    # No eligible row for context_recall -> n/a, and ragas is never invoked.
    assert captured == []
    assert out["context_recall_scored"] == "0 of 2"
    import math

    assert math.isnan(out["aggregate_context_recall"])


def test_merge_anchor_questions_normalizes_and_dedups_on_user_input(tmp_path):
    anchor_file = tmp_path / "anchors.json"
    anchor_file.write_text(
        json.dumps(
            [
                {"question": "existing q", "answer": "dup"},  # already in bank -> skip
                {
                    "question": "new anchor",
                    "answer": "ref",
                    "anchor_type": "reasoning",
                },  # merged + normalized
            ]
        )
    )

    bench = object.__new__(Benchmarker)
    bench.queries_to_answers = [{"user_input": "existing q", "reference": "r"}]
    bench.benchmarking_configs = {"anchors": {"path": str(anchor_file)}}
    bench.data_path = str(tmp_path)

    bench._merge_anchor_questions()

    merged = bench.queries_to_answers
    user_inputs = [q["user_input"] for q in merged]
    # The duplicate anchor (same user_input) is not re-added.
    assert user_inputs.count("existing q") == 1
    # The new legacy anchor is normalized onto the modern dialect and merged.
    new = next(q for q in merged if q["user_input"] == "new anchor")
    assert new["reference"] == "ref"
    assert new["anchor_type"] == "reasoning"
    assert "question" not in new and "answer" not in new


def test_merge_anchor_questions_skips_empty_anchor_file(tmp_path):
    anchor_file = tmp_path / "anchors.json"
    anchor_file.write_text("[]")  # empty/malformed -> skip, leave the bank untouched

    bench = object.__new__(Benchmarker)
    original = [{"user_input": "q", "reference": "r"}]
    bench.queries_to_answers = list(original)
    bench.benchmarking_configs = {"anchors": {"path": str(anchor_file)}}
    bench.data_path = str(tmp_path)

    bench._merge_anchor_questions()

    assert bench.queries_to_answers == original
