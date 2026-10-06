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
    effective_chunking,
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
        "chunking": {"strategy": "markdown", "chunk_overlap": 0},
        "sources": {"links": {"sitemap": {"min_pages": 150}}},
    }

    snapshot = build_ingest_config_snapshot(dm)

    assert snapshot == {
        "html_to_markdown": False,
        "categorization": True,
        "chunking_strategy": "markdown",
        "child_chunk_overlap": 0,
        "effective_chunking": {
            "path": "hierarchical",
            "strategy": "markdown",
            "parent_chunk_size": 2048,
            "child_chunk_size": 512,
            "child_chunk_overlap": 0,
        },
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
    ``_resolve_chunking_strategy``; the child overlap defaults to 20 in
    ``_resolve_chunk_overlap``; the sitemap floor defaults to 1 in the
    scraper manager; the remaining values mirror the config-seed fallbacks.
    """
    snapshot = build_ingest_config_snapshot({})

    assert snapshot == {
        "html_to_markdown": True,
        "categorization": False,
        "chunking_strategy": "sentence",
        "child_chunk_overlap": 20,
        "effective_chunking": {
            "path": "hierarchical",
            "strategy": "sentence",
            "parent_chunk_size": 2048,
            "child_chunk_size": 512,
            "child_chunk_overlap": 20,
        },
        "embedding_model": "HuggingFaceEmbeddings",
        "embedding_dimensions": 384,
        "chunk_size": 1000,
        "chunk_overlap": 150,
        "distance_metric": "cosine",
        "sitemap_min_pages": 1,
    }


def test_snapshot_covers_exactly_the_declared_key_set():
    assert set(build_ingest_config_snapshot({})) == set(INGEST_CONFIG_KEYS)


def test_snapshot_child_overlap_default_mirrors_the_node_parser():
    from src.data_manager.vectorstore.node_parsing import CHILD_CHUNK_OVERLAP

    snapshot = build_ingest_config_snapshot({})
    assert snapshot["child_chunk_overlap"] == CHILD_CHUNK_OVERLAP


def test_snapshot_reads_a_null_child_overlap_as_the_default():
    """``_resolve_chunk_overlap`` maps an empty YAML value to the default."""
    dm = {"chunking": {"chunk_overlap": None}}
    assert build_ingest_config_snapshot(dm)["child_chunk_overlap"] == 20


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
# effective_chunking
# ---------------------------------------------------------------------------


def test_effective_chunking_default_is_hierarchical_sentence():
    assert effective_chunking({}) == {
        "path": "hierarchical",
        "strategy": "sentence",
        "parent_chunk_size": 2048,
        "child_chunk_size": 512,
        "child_chunk_overlap": 20,
    }


def test_effective_chunking_character_strategy():
    dm = {
        "chunking": {"strategy": "character"},
        "chunk_size": 800,
        "chunk_overlap": 100,
    }
    assert effective_chunking(dm) == {
        "path": "character",
        "strategy": "character",
        "chunk_size": 800,
        "chunk_overlap": 100,
    }


def test_effective_chunking_unknown_strategy_uses_the_character_path():
    dm = {"chunking": {"strategy": "something-else"}}
    assert effective_chunking(dm)["path"] == "character"


def test_effective_chunking_sentence_clamps_on_the_smaller_of_parent_and_child():
    dm = {
        "chunking": {
            "strategy": "sentence",
            "parent_chunk_size": 30,
            "child_chunk_size": 512,
            "chunk_overlap": 40,
        }
    }
    assert effective_chunking(dm)["child_chunk_overlap"] == 15


def test_effective_chunking_markdown_clamps_on_child_only():
    dm = {
        "chunking": {
            "strategy": "markdown",
            "parent_chunk_size": 30,
            "child_chunk_size": 512,
            "chunk_overlap": 40,
        }
    }
    assert effective_chunking(dm)["child_chunk_overlap"] == 40


@pytest.mark.parametrize("bad", [True, "nope", -1])
def test_effective_chunking_invalid_overlap_is_reported_unclamped(bad):
    dm = {"chunking": {"strategy": "sentence", "chunk_overlap": bad}}
    assert effective_chunking(dm)["child_chunk_overlap"] == bad


@pytest.mark.parametrize("bad", [True, "nope", -1])
def test_effective_chunking_invalid_parent_size_skips_clamping(bad):
    dm = {"chunking": {"strategy": "sentence", "parent_chunk_size": bad}}
    assert effective_chunking(dm)["child_chunk_overlap"] == 20


@pytest.mark.parametrize("bad", [True, "nope", -1])
def test_effective_chunking_invalid_child_size_skips_clamping(bad):
    dm = {"chunking": {"strategy": "markdown", "child_chunk_size": bad}}
    assert effective_chunking(dm)["child_chunk_overlap"] == 20


def test_effective_chunking_null_overlap_resolves_to_the_default():
    dm = {"chunking": {"strategy": "sentence", "chunk_overlap": None}}
    assert effective_chunking(dm)["child_chunk_overlap"] == 20


@pytest.mark.parametrize("bad", [None, [], "nope", 7])
def test_effective_chunking_tolerates_a_non_mapping_config(bad):
    assert effective_chunking(bad) == effective_chunking({})


def test_effective_chunking_restated_defaults_match_the_node_parser():
    from src.data_manager.vectorstore.node_parsing import (
        CHILD_CHUNK_OVERLAP,
        DEFAULT_CHILD_CHUNK_SIZE,
        DEFAULT_PARENT_CHUNK_SIZE,
        MARKDOWN_STRATEGY,
        SENTENCE_STRATEGY,
        _clamped_overlap,
    )
    from src.utils.ingest_provenance import (
        _DEFAULT_CHILD_CHUNK_OVERLAP,
        _DEFAULT_CHILD_CHUNK_SIZE,
        _DEFAULT_CHUNKING_STRATEGY,
        _DEFAULT_PARENT_CHUNK_SIZE,
        _MARKDOWN_STRATEGY,
    )
    from src.utils.ingest_provenance import _clamped_overlap as restated_clamped_overlap

    assert _DEFAULT_PARENT_CHUNK_SIZE == DEFAULT_PARENT_CHUNK_SIZE
    assert _DEFAULT_CHILD_CHUNK_SIZE == DEFAULT_CHILD_CHUNK_SIZE
    assert _DEFAULT_CHILD_CHUNK_OVERLAP == CHILD_CHUNK_OVERLAP
    assert _DEFAULT_CHUNKING_STRATEGY == SENTENCE_STRATEGY
    assert _MARKDOWN_STRATEGY == MARKDOWN_STRATEGY
    for chunk_size, overlap in [(2048, 20), (512, 300), (30, 40), (0, 5)]:
        assert restated_clamped_overlap(chunk_size, overlap) == _clamped_overlap(
            chunk_size, overlap
        )


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
    current = {"categorization": False, "future_flag": "on"}

    drift = compare_ingest_config(current, at_ingest)

    assert drift == [
        {"key": "future_flag", "current": "on", "at_ingest": None},
    ]


def test_compare_skips_a_declared_key_the_older_snapshot_did_not_record():
    """A snapshot written before a key was declared is silent about that key.

    The builder always emits every declared key, so a declared key absent from
    the recorded snapshot means "not recorded", not "changed". Without this,
    adding a key to the snapshot flags every older ingest as drifted.
    """
    at_ingest = build_ingest_config_snapshot({})
    del at_ingest["child_chunk_overlap"]
    del at_ingest["effective_chunking"]
    current = build_ingest_config_snapshot({"chunking": {"chunk_overlap": 0}})

    assert compare_ingest_config(current, at_ingest) == []


def test_compare_treats_an_empty_snapshot_as_no_comparison():
    """An ingest run recorded before the snapshot existed must not read as total drift."""
    current = build_ingest_config_snapshot({})
    assert compare_ingest_config(current, {}) == []
    assert compare_ingest_config(current, None) == []


def test_compare_skips_effective_chunking_absent_from_an_older_snapshot():
    at_ingest = build_ingest_config_snapshot({})
    del at_ingest["effective_chunking"]
    current = build_ingest_config_snapshot({"chunking": {"child_chunk_size": 256}})

    drift = compare_ingest_config(current, at_ingest)

    assert "effective_chunking" not in {entry["key"] for entry in drift}


def test_compare_reports_effective_chunking_when_both_sides_carry_it():
    at_ingest = build_ingest_config_snapshot({})
    current = build_ingest_config_snapshot({"chunking": {"child_chunk_size": 256}})

    drift = compare_ingest_config(current, at_ingest)

    assert [entry for entry in drift if entry["key"] == "effective_chunking"] == [
        {
            "key": "effective_chunking",
            "current": effective_chunking({"chunking": {"child_chunk_size": 256}}),
            "at_ingest": effective_chunking({}),
        }
    ]
