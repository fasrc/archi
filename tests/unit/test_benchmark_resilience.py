"""Unit tests for the benchmark run-loop resilience helpers.

Covers the `benchmark-run-resilience` capability of the openspec change
`harden-benchmark-and-agent-resilience`:
- per-question failures are captured as marked entries, never propagated;
- context-overflow degraded answers are classified as degraded (not clean);
- failed/degraded rows are excluded from aggregates and human-eval consumers;
- an all-failed configuration yields `n/a` aggregates instead of empty RAGAS.
"""

from __future__ import annotations

import math

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


_QITEM = {"user_input": "how do I do X?", "reference": "ref", "sources": []}
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
    # RAGAS input uses the modern ragas 0.3.5 dialect, not legacy columns.
    assert set(bundle["dataset_result"]) == {
        "user_input",
        "retrieved_contexts",
        "response",
        "reference",
    }
    assert bundle["dataset_result"]["reference"] == "ref"
    assert bundle["dataset_result"]["response"] == "an answer"
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
    # and it must NOT stamp `matched` onto its sources (Codex F4), or the HTML
    # report would show a truncated-context answer as source-correct.
    assert "matched" not in bundle["q_results"]["reference_sources_metadata"][0]


def test_answer_and_score_exception_is_isolated():
    def _boom(**kw):
        raise RuntimeError("context length is only 32768")

    agent = _StubBenchmarker(chain=_boom)
    bundle = agent._answer_and_score_question(_QITEM, 1, _MODES)
    assert bundle["q_results"]["status"] == FAILED
    assert "RuntimeError" in bundle["q_results"]["error"]
    assert bundle["dataset_result"] is None
    assert bundle["matches"] is None


def test_answer_and_score_draft_row_display_safe_but_ragas_reference_raw():
    """A draft row (empty `reference`) is scorable: its ragas payload keeps the raw
    empty `reference` (which drives context-metric eligibility), but the
    human-facing `reference_answer` is an "N/A" sentinel so the result / Argilla
    record never carries a blank required field (adversarial review)."""
    draft_item = {"user_input": "draft q", "sources": []}  # no `reference`
    agent = _StubBenchmarker(chain=lambda **kw: _result())
    bundle = agent._answer_and_score_question(draft_item, 1, _MODES)
    assert bundle["q_results"]["status"] == OK
    # display / Argilla sink gets a non-empty sentinel...
    assert bundle["q_results"]["reference_answer"] == "N/A"
    # ...while the ragas payload keeps the raw empty reference for eligibility.
    assert bundle["dataset_result"]["reference"] == ""


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


def test_source_hits_zero_reference_is_not_a_strict_hit():
    # A row with no expected sources (a `should_refuse` anchor) cannot hit or
    # miss a source. `all([])` is vacuously true, so the original semantics
    # booked a FREE strict hit for it — inflating `source_accuracy` no matter
    # what the model answered. Such a row must contribute nothing, exactly like
    # a failed row; `_source_scorable_count` also keeps it out of the denominator.
    assert source_hits([], []) == (0, 0)


def test_source_hits_zero_reference_is_not_confused_with_a_perfect_hit():
    perfect = source_hits([True], [{"url": "a"}])
    assert perfect == (1, 1)
    # The bug: a zero-reference row ALSO returned strict=1, making it
    # indistinguishable from a row whose every expected source was retrieved.
    assert source_hits([], [])[1] != perfect[1]
    # A declared reference that went unmatched is still a real miss, not a no-op.
    assert source_hits([False], [{"url": "a"}]) == (0, 0)


def test_source_hits_relative_only():
    # one of two references matched -> relative hit, not strict
    assert source_hits([True, False], [{"url": "a"}, {"url": "b"}]) == (1, 0)


def test_source_hits_strict():
    assert source_hits([True, True], [{"url": "a"}, {"url": "b"}]) == (1, 1)


# --- build_ragas_aggregates / build_source_aggregates -----------------------


def test_build_ragas_aggregates_nan_when_none():
    aggs = build_ragas_aggregates(None)
    assert set(aggs) == {
        "aggregate_answer_relevancy",
        "aggregate_faithfulness",
        "aggregate_context_precision",
        "aggregate_context_recall",
    }
    assert all(isinstance(v, float) and math.isnan(v) for v in aggs.values())


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


def test_build_source_aggregates_zero_questions_is_numeric():
    assert build_source_aggregates(0.0, 0.0, 0) == {
        "relative_source_accuracy": 0.0,
        "source_accuracy": 0.0,
        "source_scored_count": 0,
    }


def test_build_source_aggregates_emits_its_denominator():
    # The HTML report used to re-derive the hit count as int(len(questions) * acc).
    # Now that zero-source rows are excluded, len(questions) is the WRONG
    # denominator, so the count that was actually divided by has to travel with
    # the score (mirrors the per-metric `scored_counts` RAGAS already emits).
    assert build_source_aggregates(3.0, 1.0, 4)["source_scored_count"] == 4


def test_build_source_aggregates_divides_by_total_count():
    aggs = build_source_aggregates(3.0, 1.0, 4)
    assert aggs["relative_source_accuracy"] == 0.75
    assert aggs["source_accuracy"] == 0.25


# --- _process_config: loop orchestration ------------------------------------


class _ConfigStub(Benchmarker):
    """Skip __init__; drive _process_config with canned per-question bundles."""

    def __init__(self, queries, bundles):
        self.queries_to_answers = queries
        self.required_fields = ["user_input"]
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
        "dataset_result": {"user_input": "q", "reference": "r"},
        "matches": [True],
    }


def _miss_bundle():
    # A real question whose declared source was NOT retrieved.
    return {
        "q_results": {
            "status": OK,
            "question": "q",
            "reference_sources_metadata": [{"url": "x"}],
        },
        "dataset_result": {"user_input": "q", "reference": "r"},
        "matches": [False],
    }


def _zero_source_bundle():
    # A `should_refuse` anchor: declares no sources, so nothing to match.
    return {
        "q_results": {
            "status": OK,
            "question": "refuse",
            "reference_sources_metadata": [],
        },
        "dataset_result": {"user_input": "refuse", "reference": "r"},
        "matches": [],
    }


def _fail_bundle():
    return {
        "q_results": {"status": FAILED, "error": "boom"},
        "dataset_result": None,
        "matches": None,
    }


def test_process_config_sources_aggregate():
    # The query must DECLARE the source its bundle claims to have matched:
    # `reference_sources_metadata` is derived from `sources`, so a row cannot match
    # a source it never declared, and `sources` is now the aggregate's denominator.
    agent = _ConfigStub(
        queries=[{"user_input": "q", "sources": ["x"]}], bundles=[_ok_bundle()]
    )
    qwr, total = agent._process_config({"SOURCES"})
    assert set(qwr) == {"question_1"}
    assert total["relative_source_accuracy"] == 1.0
    assert total["source_accuracy"] == 1.0
    assert total["source_scored_count"] == 1


def test_process_config_excludes_zero_source_row_from_source_aggregate():
    # A `should_refuse` anchor beside one real question. Before the fix the anchor
    # booked a free strict hit AND sat in the denominator, so a run that matched
    # the one real source reported source_accuracy = 2/2 = 1.0 either way — the
    # anchor's "score" was pure fiction. It must now be invisible to both.
    agent = _ConfigStub(
        queries=[
            {"user_input": "q", "sources": ["x"]},
            {"user_input": "refuse", "sources": []},
        ],
        bundles=[_ok_bundle(), _zero_source_bundle()],
    )
    _, total = agent._process_config({"SOURCES"})
    assert total["source_scored_count"] == 1
    assert total["source_accuracy"] == 1.0
    assert total["relative_source_accuracy"] == 1.0


def test_process_config_zero_source_row_cannot_rescue_a_miss():
    # The anchor must not paper over a real retrieval miss: one declared source,
    # unmatched -> 0.0, not the 0.5 the free strict hit used to manufacture.
    agent = _ConfigStub(
        queries=[
            {"user_input": "q", "sources": ["x"]},
            {"user_input": "refuse", "sources": []},
        ],
        bundles=[_miss_bundle(), _zero_source_bundle()],
    )
    _, total = agent._process_config({"SOURCES"})
    assert total["source_scored_count"] == 1
    assert total["source_accuracy"] == 0.0


def test_process_config_all_failed_ragas_is_nan():
    agent = _ConfigStub(queries=[{"user_input": "q"}], bundles=[_fail_bundle()])
    qwr, total = agent._process_config({"RAGAS"})
    assert qwr["question_1"]["status"] == FAILED
    # no scorable RAGAS input -> aggregates are NaN, not an empty-Dataset crash
    assert math.isnan(total["aggregate_faithfulness"])
    assert math.isnan(total["aggregate_answer_relevancy"])


def test_process_config_skips_invalid_items():
    agent = _ConfigStub(queries=["not-a-dict", {"no_question": 1}], bundles=[])
    qwr, total = agent._process_config({"SOURCES"})
    # both invalid items are skipped; the answer path is never reached
    assert qwr == {}
    # denominator is the total question count (2); no hits -> 0.0, numeric
    assert total["relative_source_accuracy"] == 0.0


def test_process_config_passes_only_scorable_to_ragas():
    """RAGAS scoring must receive exactly the scorable rows, keyed by question
    (not the full result set, and by key never positionally — Codex F1/F5)."""
    captured = {}

    class _RagasStub(_ConfigStub):
        def get_ragas_results(self, rows, keys, results_by_key):
            captured["keys"] = list(keys)
            captured["rows"] = list(rows)
            captured["results_keys"] = list(results_by_key.keys())
            return {"aggregate_faithfulness": 1.0}

    agent = _RagasStub(
        queries=[{"user_input": "a"}, {"user_input": "b"}],
        bundles=[_ok_bundle(), _fail_bundle()],
    )
    _, total = agent._process_config({"RAGAS"})
    # question_2 failed (no ragas input) -> only question_1 reaches RAGAS, keyed.
    assert captured["keys"] == ["question_1"]
    assert captured["results_keys"] == ["question_1"]
    # exactly the scorable row's modern dataset_result is scored.
    assert captured["rows"] == [{"user_input": "q", "reference": "r"}]
    assert total["aggregate_faithfulness"] == 1.0


# --- answer_correctness: A/B pairing ----------------------------------------


def test_pair_ab_results_carries_answer_correctness(monkeypatch):
    """A/B pairing builds its payload from a fixed metric-name list, so a metric
    missing from that list is dropped from both the paired scores and the
    per-metric winner — the comparison would silently ignore it."""

    def _row(ac):
        return {
            "question": "q",
            "reference_answer": "r",
            "status": OK,
            "answer_correctness": ac,
        }

    monkeypatch.setattr(
        ResultHandler,
        "results",
        [
            {"single_question_results": {"question_1": _row(0.9)}},
            {"single_question_results": {"question_1": _row(0.4)}},
        ],
    )
    paired = ResultHandler.pair_ab_results(0, 1)

    assert len(paired) == 1
    assert paired[0].ragas_a["answer_correctness"] == 0.9
    assert paired[0].ragas_b["answer_correctness"] == 0.4
    assert paired[0].winner_by_metric["answer_correctness"] == "a"
