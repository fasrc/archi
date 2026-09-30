"""Unit tests for the ingest-configuration snapshot and drift comparison.

The snapshot is what lets the status board say a flag was used *at ingest*
rather than *currently configured*, so these tests pin both the flag set and
every default. The defaults are not arbitrary: they mirror the values the
ingest path itself applies, so a snapshot can never report a default the ingest
does not use.
"""

import pytest

from src.utils.ingest_provenance import (
    INGEST_CONFIG_KEYS,
    build_ingest_config_snapshot,
    compare_ingest_config,
)

# ---------------------------------------------------------------------------
# build_ingest_config_snapshot
# ---------------------------------------------------------------------------


def test_snapshot_reads_every_flag_from_a_full_config():
    dm = {
        "embedding_name": "OpenAIEmbeddings",
        "embedding_class_map": {"OpenAIEmbeddings": {"dimensions": 1536}},
        "chunk_size": 800,
        "chunk_overlap": 120,
        "distance_metric": "l2",
        "processing": {
            "html_to_markdown": {"enabled": False},
            "categorization": {"enabled": True},
        },
        "chunking": {"strategy": "markdown"},
        "sources": {"links": {"sitemap": {"min_pages": 150}}},
    }

    snapshot = build_ingest_config_snapshot(dm)

    assert snapshot == {
        "html_to_markdown": False,
        "categorization": True,
        "chunking_strategy": "markdown",
        "embedding_model": "OpenAIEmbeddings",
        "embedding_dimensions": 1536,
        "chunk_size": 800,
        "chunk_overlap": 120,
        "distance_metric": "l2",
        "sitemap_min_pages": 150,
    }


def test_snapshot_applies_the_ingest_paths_own_defaults_when_keys_are_absent():
    """An empty config must yield the shipped defaults, not None.

    html_to_markdown defaults true and categorization defaults false in
    ``build_persistence_service``; chunking defaults to ``sentence`` in
    ``_resolve_chunking_strategy``; the sitemap floor defaults to 1 in the
    scraper manager; the remaining values mirror the config-seed fallbacks.
    """
    snapshot = build_ingest_config_snapshot({})

    assert snapshot == {
        "html_to_markdown": True,
        "categorization": False,
        "chunking_strategy": "sentence",
        "embedding_model": "HuggingFaceEmbeddings",
        "embedding_dimensions": 384,
        "chunk_size": 1000,
        "chunk_overlap": 150,
        "distance_metric": "cosine",
        "sitemap_min_pages": 1,
    }


def test_snapshot_covers_exactly_the_declared_key_set():
    assert set(build_ingest_config_snapshot({})) == set(INGEST_CONFIG_KEYS)


@pytest.mark.parametrize("bad", [None, [], "nope", 7])
def test_snapshot_tolerates_a_non_mapping_config(bad):
    """A malformed config must degrade to defaults, never raise.

    The snapshot is provenance; it must not be able to fail an ingest.
    """
    assert build_ingest_config_snapshot(bad) == build_ingest_config_snapshot({})


@pytest.mark.parametrize("section", ["processing", "chunking", "sources"])
def test_snapshot_tolerates_a_null_subsection(section):
    dm = {section: None}
    assert build_ingest_config_snapshot(dm) == build_ingest_config_snapshot({})


def test_snapshot_reads_dimensions_for_the_named_embedding_only():
    dm = {
        "embedding_name": "HuggingFaceEmbeddings",
        "embedding_class_map": {
            "HuggingFaceEmbeddings": {"dimensions": 768},
            "OpenAIEmbeddings": {"dimensions": 1536},
        },
    }
    assert build_ingest_config_snapshot(dm)["embedding_dimensions"] == 768


def test_snapshot_falls_back_when_the_embedding_is_missing_from_the_map():
    dm = {"embedding_name": "SomethingElse", "embedding_class_map": {}}
    assert build_ingest_config_snapshot(dm)["embedding_dimensions"] == 384


def test_snapshot_coerces_truthy_flag_values_to_bool():
    """Flags must round-trip through JSONB as booleans, not as 1/0 or "yes"."""
    dm = {
        "processing": {
            "html_to_markdown": {"enabled": 1},
            "categorization": {"enabled": 0},
        }
    }
    snapshot = build_ingest_config_snapshot(dm)
    assert snapshot["html_to_markdown"] is True
    assert snapshot["categorization"] is False


# ---------------------------------------------------------------------------
# compare_ingest_config
# ---------------------------------------------------------------------------


def test_compare_reports_no_drift_for_equal_snapshots():
    snapshot = build_ingest_config_snapshot({})
    assert compare_ingest_config(snapshot, snapshot) == []


def test_compare_names_a_single_differing_flag_with_both_values():
    at_ingest = build_ingest_config_snapshot({})
    current = dict(at_ingest, categorization=True)

    drift = compare_ingest_config(current, at_ingest)

    assert drift == [
        {"key": "categorization", "current": True, "at_ingest": False},
    ]


def test_compare_reports_every_differing_flag():
    at_ingest = build_ingest_config_snapshot({})
    current = dict(at_ingest, categorization=True, chunk_size=512)

    drift = compare_ingest_config(current, at_ingest)

    assert {entry["key"] for entry in drift} == {"categorization", "chunk_size"}


def test_compare_orders_drift_by_the_declared_key_order():
    """Stable ordering keeps the rendered warning from reshuffling between loads."""
    at_ingest = build_ingest_config_snapshot({})
    current = dict(at_ingest, sitemap_min_pages=9, html_to_markdown=False)

    drift = compare_ingest_config(current, at_ingest)

    assert [entry["key"] for entry in drift] == [
        "html_to_markdown",
        "sitemap_min_pages",
    ]


def test_compare_reports_a_key_present_on_one_side_only():
    at_ingest = {"categorization": False}
    current = {"categorization": False, "chunking_strategy": "sentence"}

    drift = compare_ingest_config(current, at_ingest)

    assert drift == [
        {"key": "chunking_strategy", "current": "sentence", "at_ingest": None},
    ]


def test_compare_treats_an_empty_snapshot_as_no_comparison():
    """An ingest run recorded before the snapshot existed must not read as total drift."""
    current = build_ingest_config_snapshot({})
    assert compare_ingest_config(current, {}) == []
    assert compare_ingest_config(current, None) == []
