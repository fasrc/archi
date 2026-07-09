"""Regression: RAGAS-only runs must not require source match fields.

`prepare_match_fields` enforces one match field per reference source and raises
on a mismatch. A RAGAS-only bank legitimately includes zero-source rows (e.g.
`should_refuse` questions), which previously aborted the whole run even though
SOURCES scoring was off. `_resolve_reference_match_fields` gates that work on
SOURCES mode so such banks stay consumable.
"""

import pytest

from src.bin.service_benchmark import Benchmarker


def _benchmarker():
    return Benchmarker.__new__(Benchmarker)


def test_ragas_only_skips_match_fields_for_zero_source_row():
    bench = _benchmarker()

    # A should_refuse row: no sources, but a match field is declared.
    question_item = {"user_input": "q", "sources": [], "source_match_field": ["url"]}

    # prepare_match_fields would raise here (1 field != 0 sources); the gate must
    # avoid calling it at all when SOURCES is not in the run.
    def _boom(_item):
        raise AssertionError("prepare_match_fields must not run in RAGAS-only mode")

    bench.prepare_match_fields = _boom  # type: ignore[method-assign]

    match_fields, formatted = bench._resolve_reference_match_fields(
        question_item, reference_sources=[], modes_being_run={"RAGAS"}
    )
    assert match_fields == []
    assert formatted == []


def test_sources_mode_still_computes_match_fields():
    bench = _benchmarker()

    question_item = {
        "user_input": "q",
        "sources": ["https://example.org/doc"],
        "source_match_field": ["url"],
    }

    match_fields, formatted = bench._resolve_reference_match_fields(
        question_item,
        reference_sources=["https://example.org/doc"],
        modes_being_run={"SOURCES"},
    )
    assert match_fields == ["url"]
    assert formatted == [{"url": "https://example.org/doc"}]


# --- zero-source rows under SOURCES mode ------------------------------------
#
# The RAGAS-only gate above does not help a run with `modes: [RAGAS, SOURCES]`
# (the anchor bank's `should_refuse` row declares `sources: []` but still carries
# `source_match_field: ["url"]`). Pairing one match field against zero sources is
# not a mismatch to reject — there is simply nothing to pair. `source_hits`
# already treats an empty match list as a clean zero-reference row rather than a
# failure, so `prepare_match_fields` must let it through.


def test_prepare_match_fields_returns_empty_for_zero_source_row():
    bench = _benchmarker()
    question_item = {"user_input": "q", "sources": [], "source_match_field": ["url"]}
    assert bench.prepare_match_fields(question_item) == []


def test_zero_source_row_survives_sources_mode():
    bench = _benchmarker()
    question_item = {"user_input": "q", "sources": [], "source_match_field": ["url"]}

    match_fields, formatted = bench._resolve_reference_match_fields(
        question_item,
        reference_sources=[],
        modes_being_run={"RAGAS", "SOURCES"},
    )
    assert match_fields == []
    assert formatted == []


def test_prepare_match_fields_still_raises_on_genuine_mismatch():
    bench = _benchmarker()
    question_item = {
        "user_input": "q",
        "sources": ["a", "b", "c"],
        "source_match_field": ["url", "file_name"],
    }
    with pytest.raises(ValueError):
        bench.prepare_match_fields(question_item)
