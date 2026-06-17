"""Unit tests for the title-/filename-aware benchmark query set (task 5.1).

These tests exercise the curated query set and its loader, which the benchmark
harness (``src/bin/service_benchmark.py``) merges into a deployment's query set
so before/after runs measure recall on title-only and filename-only queries.
"""

import json

import pytest

from src.bin.benchmark_query_sets import (CATEGORY_FILENAME_ONLY,
                                          CATEGORY_MATCH_FIELDS,
                                          CATEGORY_TITLE_ONLY,
                                          REQUIRED_QUERY_FIELDS,
                                          TITLE_AWARE_CATEGORIES,
                                          TITLE_AWARE_QUERY_SET_PATH,
                                          load_title_aware_query_set,
                                          merge_query_sets)


def test_query_set_file_exists_and_is_json_list():
    assert TITLE_AWARE_QUERY_SET_PATH.exists()
    with open(TITLE_AWARE_QUERY_SET_PATH, "r") as f:
        data = json.load(f)
    assert isinstance(data, list) and data


def test_loader_returns_validated_items():
    query_set = load_title_aware_query_set()
    assert isinstance(query_set, list) and query_set
    for item in query_set:
        for field in REQUIRED_QUERY_FIELDS:
            assert item.get(field), f"missing field {field} in {item}"
        assert item["category"] in TITLE_AWARE_CATEGORIES


def test_query_set_includes_both_categories():
    categories = {item["category"] for item in load_title_aware_query_set()}
    assert CATEGORY_TITLE_ONLY in categories
    assert CATEGORY_FILENAME_ONLY in categories


def test_match_field_aligns_with_category():
    for item in load_title_aware_query_set():
        expected = CATEGORY_MATCH_FIELDS[item["category"]]
        assert item["source_match_field"] == expected


def test_title_only_uses_display_name_and_filename_only_uses_file_name():
    assert CATEGORY_MATCH_FIELDS[CATEGORY_TITLE_ONLY] == "display_name"
    assert CATEGORY_MATCH_FIELDS[CATEGORY_FILENAME_ONLY] == "file_name"


def test_merge_query_sets_appends_without_mutation():
    base = [{"question": "existing", "answer": "a"}]
    extra = load_title_aware_query_set()
    merged = merge_query_sets(base, extra)
    assert merged[: len(base)] == base
    assert merged[len(base) :] == extra
    assert len(merged) == len(base) + len(extra)
    # inputs untouched
    assert base == [{"question": "existing", "answer": "a"}]
    assert extra == load_title_aware_query_set()


def test_loader_rejects_empty_query_set(tmp_path):
    bad = tmp_path / "empty.json"
    bad.write_text("[]")
    with pytest.raises(ValueError):
        load_title_aware_query_set(bad)


def test_loader_rejects_unknown_category(tmp_path):
    bad = tmp_path / "bad_category.json"
    bad.write_text(
        json.dumps(
            [
                {
                    "question": "q",
                    "category": "body_only",
                    "sources": "doc",
                    "source_match_field": "display_name",
                }
            ]
        )
    )
    with pytest.raises(ValueError):
        load_title_aware_query_set(bad)


def test_loader_rejects_mismatched_match_field(tmp_path):
    bad = tmp_path / "bad_field.json"
    bad.write_text(
        json.dumps(
            [
                {
                    "question": "q",
                    "category": CATEGORY_TITLE_ONLY,
                    "sources": "doc",
                    "source_match_field": "file_name",
                }
            ]
        )
    )
    with pytest.raises(ValueError):
        load_title_aware_query_set(bad)


def test_loader_rejects_missing_required_field(tmp_path):
    bad = tmp_path / "missing_field.json"
    bad.write_text(
        json.dumps(
            [
                {
                    "category": CATEGORY_TITLE_ONLY,
                    "sources": "doc",
                    "source_match_field": "display_name",
                }
            ]
        )
    )
    with pytest.raises(ValueError):
        load_title_aware_query_set(bad)
