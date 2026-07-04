"""Unit tests for the benchmark run-loop resilience helpers.

Covers the `benchmark-run-resilience` capability of the openspec change
`harden-benchmark-and-agent-resilience`:
- per-question failures are captured as marked entries, never propagated;
- context-overflow degraded answers are classified as degraded (not clean);
- failed/degraded rows are excluded from aggregates and human-eval consumers;
- an all-failed configuration yields `n/a` aggregates instead of empty RAGAS.
"""

from __future__ import annotations

from src.utils.benchmark_resilience import (
    DEGRADED,
    FAILED,
    OK,
    build_failure_entry,
    build_ragas_aggregates,
    build_source_aggregates,
    classify_metadata,
    is_scorable,
    scorable_items,
    source_hits,
)

# --- classify_metadata: degraded detection (F3 / PR#91 F2) ------------------


def test_clean_answer_is_ok():
    assert classify_metadata({"event_type": "final"}) == OK
    assert classify_metadata({}) == OK
    assert classify_metadata(None) == OK


def test_context_overflow_fallback_is_degraded():
    assert classify_metadata({"error_type": "context_overflow"}) == DEGRADED


def test_context_overflow_retry_is_degraded():
    assert classify_metadata({"context_overflow_retry": True}) == DEGRADED


# --- build_failure_entry: exception capture ---------------------------------


def test_build_failure_entry_marks_and_captures_error():
    entry = build_failure_entry(
        question="q?", reference_answer="ref", error=ValueError("boom")
    )
    assert entry["status"] == FAILED
    assert entry["question"] == "q?"
    assert "ValueError" in entry["error"]
    assert "boom" in entry["error"]
    assert entry["answer"] == ""


# --- is_scorable / scorable_items -------------------------------------------


def test_is_scorable_only_true_for_ok():
    assert is_scorable({"status": OK}) is True
    assert is_scorable({}) is True  # unmarked legacy rows are treated as ok
    assert is_scorable({"status": FAILED}) is False
    assert is_scorable({"status": DEGRADED}) is False


def test_scorable_items_filters_failed_and_degraded():
    qwr = {
        "question_1": {"status": OK, "answer": "a"},
        "question_2": {"status": FAILED, "error": "x"},
        "question_3": {"status": DEGRADED, "answer": "trunc"},
        "question_4": {"answer": "legacy"},  # unmarked -> scorable
    }
    kept = scorable_items(qwr)
    assert set(kept) == {"question_1", "question_4"}


# --- _answer_and_score_question: isolation + degraded marking ---------------

from src.bin.service_benchmark import Benchmarker, ResultHandler  # noqa: E402


class _FakeDoc:
    def __init__(self, content, metadata):
        self.page_content = content
        self.metadata = metadata


class _StubBenchmarker(Benchmarker):
    """Skip Benchmarker.__init__; stub only what _answer_and_score_question needs."""

    def __init__(self, chain):
        self.chain = chain

    def prepare_messages(self, raw_messages):
        return list(raw_messages)

    def _resolve_reference_match_fields(
        self, question_item, reference_sources, modes_being_run
    ):
        # Non-empty so the SOURCES per-source `matched` loop is exercised.
        return (["url"], [{"url": "https://example/doc"}])

    def get_source_results(self, result, formatted_reference_sources):
        return [True]


_QITEM = {"question": "how do I do X?", "answer": "ref", "sources": []}
_MODES = {"RAGAS", "SOURCES"}


def _result(answer="an answer", metadata=None):
    return {
        "answer": answer,
        "messages": [("AI", answer)],
        "source_documents": [_FakeDoc("chunk text", {"url": "https://example/doc"})],
        "metadata": metadata or {},
    }


def test_answer_and_score_clean_success():
    agent = _StubBenchmarker(chain=lambda **kw: _result())
    bundle = agent._answer_and_score_question(_QITEM, 1, _MODES)
    assert bundle["q_results"]["status"] == OK
    assert bundle["q_results"]["answer"] == "an answer"
    assert bundle["dataset_result"] is not None  # RAGAS input built
    assert bundle["matches"] == [True]
    # per-source match stamped, and source metadata/truncation captured
    assert bundle["q_results"]["reference_sources_metadata"][0]["matched"] is True
    assert bundle["q_results"]["sources_metadata"] == [{"url": "https://example/doc"}]
    assert bundle["q_results"]["sources_trunc_content"] == ["chunk text"]


def test_answer_and_score_degraded_is_excluded():
    agent = _StubBenchmarker(
        chain=lambda **kw: _result(metadata={"error_type": "context_overflow"})
    )
    bundle = agent._answer_and_score_question(_QITEM, 1, _MODES)
    assert bundle["q_results"]["status"] == DEGRADED
    # A degraded answer feeds neither RAGAS nor source scoring.
    assert bundle["dataset_result"] is None
    assert bundle["matches"] is None


def test_answer_and_score_exception_is_isolated():
    def _boom(**kw):
        raise RuntimeError("context length is only 32768")

    agent = _StubBenchmarker(chain=_boom)
    bundle = agent._answer_and_score_question(_QITEM, 1, _MODES)
    assert bundle["q_results"]["status"] == FAILED
    assert "RuntimeError" in bundle["q_results"]["error"]
    assert bundle["dataset_result"] is None
    assert bundle["matches"] is None


# --- pair_ab_results excludes failed/degraded rows (F4) ---------------------


def test_pair_ab_results_skips_non_scorable(monkeypatch):
    def _row(status, ar):
        return {
            "question": "q",
            "reference_answer": "r",
            "status": status,
            "answer_relevancy": ar,
        }

    results = [
        {
            "single_question_results": {
                "question_1": _row(OK, 0.9),
                "question_2": _row(FAILED, 0.0),
            }
        },
        {
            "single_question_results": {
                "question_1": _row(OK, 0.8),
                "question_2": _row(OK, 0.7),
            }
        },
    ]
    monkeypatch.setattr(ResultHandler, "results", results)
    paired = ResultHandler.pair_ab_results(0, 1)
    # question_2 is FAILED in config A -> excluded; only question_1 pairs.
    assert len(paired) == 1


# --- source_hits ------------------------------------------------------------


def test_source_hits_none_contributes_nothing():
    assert source_hits(None, [{"url": "x"}]) == (0, 0)
    assert source_hits([], [{"url": "x"}]) == (0, 0)


def test_source_hits_relative_only():
    # one of two references matched -> relative hit, not strict
    assert source_hits([True, False], [{"url": "a"}, {"url": "b"}]) == (1, 0)


def test_source_hits_strict():
    assert source_hits([True, True], [{"url": "a"}, {"url": "b"}]) == (1, 1)


# --- build_ragas_aggregates / build_source_aggregates -----------------------


def test_build_ragas_aggregates_na_when_none():
    aggs = build_ragas_aggregates(None)
    assert aggs == {
        "aggregate_answer_relevancy": "n/a",
        "aggregate_faithfulness": "n/a",
        "aggregate_context_precision": "n/a",
        "aggregate_context_recall": "n/a",
    }


def test_build_ragas_aggregates_means_when_present():
    import pandas as pd

    df = pd.DataFrame(
        {
            "answer_relevancy": [1.0, 0.0],
            "faithfulness": [0.5, 0.5],
            "context_precision": [1.0, 1.0],
            "context_recall": [0.0, 1.0],
        }
    )
    aggs = build_ragas_aggregates(df)
    assert aggs["aggregate_answer_relevancy"] == 0.5
    assert aggs["aggregate_faithfulness"] == 0.5


def test_build_source_aggregates_na_when_no_scorable():
    assert build_source_aggregates(0.0, 0.0, 0) == {
        "relative_source_accuracy": "n/a",
        "source_accuracy": "n/a",
    }


def test_build_source_aggregates_divides_by_scorable_count():
    aggs = build_source_aggregates(3.0, 1.0, 4)
    assert aggs["relative_source_accuracy"] == 0.75
    assert aggs["source_accuracy"] == 0.25


# --- _process_config: loop orchestration ------------------------------------


class _ConfigStub(Benchmarker):
    """Skip __init__; drive _process_config with canned per-question bundles."""

    def __init__(self, queries, bundles):
        self.queries_to_answers = queries
        self.required_fields = ["question"]
        self._bundles = list(bundles)

    def _answer_and_score_question(self, question_item, question_id, modes_being_run):
        return self._bundles.pop(0)


def _ok_bundle():
    return {
        "q_results": {
            "status": OK,
            "question": "q",
            "reference_sources_metadata": [{"url": "x"}],
        },
        "dataset_result": {"question": "q"},
        "matches": [True],
    }


def _fail_bundle():
    return {
        "q_results": {"status": FAILED, "error": "boom"},
        "dataset_result": None,
        "matches": None,
    }


def test_process_config_sources_aggregate():
    agent = _ConfigStub(queries=[{"question": "q"}], bundles=[_ok_bundle()])
    qwr, total = agent._process_config({"SOURCES"})
    assert set(qwr) == {"question_1"}
    assert total["relative_source_accuracy"] == 1.0
    assert total["source_accuracy"] == 1.0


def test_process_config_all_failed_ragas_is_na():
    agent = _ConfigStub(queries=[{"question": "q"}], bundles=[_fail_bundle()])
    qwr, total = agent._process_config({"RAGAS"})
    assert qwr["question_1"]["status"] == FAILED
    # no scorable RAGAS input -> aggregates are n/a, not an empty-Dataset crash
    assert total["aggregate_faithfulness"] == "n/a"
    assert total["aggregate_answer_relevancy"] == "n/a"


def test_process_config_skips_invalid_items():
    agent = _ConfigStub(queries=["not-a-dict", {"no_question": 1}], bundles=[])
    qwr, total = agent._process_config({"SOURCES"})
    # both invalid items are skipped; the answer path is never reached
    assert qwr == {}
    assert total["relative_source_accuracy"] == "n/a"
