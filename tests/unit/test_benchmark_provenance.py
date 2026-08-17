"""Provenance helpers for benchmark reports.

A benchmark report is only usable as evidence if it records the conditions the
run actually executed under. Two of those conditions were previously recorded
from the wrong source:

* The agent reads its configuration from Postgres (``config_access.get_full_config``),
  but ``ResultHandler.handle_results`` re-read the YAML file from disk at report
  time. When the two diverged, the report labelled the run with settings it never
  used -- a 8192-token run was recorded as 32768.
* ``corpus_snapshot_id`` is a fresh UUID per invocation, so it can distinguish
  invocations but can never show that two runs saw the *same* corpus.

These helpers are pure so the report writer stays a thin call site.
"""

import pytest

from src.utils.benchmark_provenance import config_divergence, corpus_fingerprint


class TestConfigDivergence:
    """Dotted paths where the intended config and the running config disagree."""

    def test_identical_configs_have_no_divergence(self):
        cfg = {"services": {"chat_app": {"context_editing": {"context_window": 32768}}}}
        assert config_divergence(cfg, dict(cfg)) == []

    def test_reports_the_regression_that_mislabelled_the_8192_run(self):
        """The file said 32768; Postgres -- what the agent read -- said 8192."""
        intended = {
            "services": {
                "chat_app": {"context_editing": {"context_window": 32768, "keep": 1}}
            }
        }
        running = {
            "services": {
                "chat_app": {"context_editing": {"context_window": 8192, "keep": 1}}
            }
        }

        assert config_divergence(intended, running) == [
            "services.chat_app.context_editing.context_window"
        ]

    def test_reports_a_key_missing_from_the_running_config(self):
        intended = {"services": {"chat_app": {"recursion_limit": 50}}}
        running = {"services": {"chat_app": {}}}

        assert config_divergence(intended, running) == [
            "services.chat_app.recursion_limit"
        ]

    def test_reports_a_key_present_only_in_the_running_config(self):
        intended = {"services": {"chat_app": {}}}
        running = {"services": {"chat_app": {"recursion_limit": 50}}}

        assert config_divergence(intended, running) == [
            "services.chat_app.recursion_limit"
        ]

    def test_reports_every_differing_path_sorted(self):
        intended = {"b": {"y": 1}, "a": 1}
        running = {"b": {"y": 2}, "a": 2}

        assert config_divergence(intended, running) == ["a", "b.y"]

    def test_none_and_empty_container_are_the_same_setting(self):
        """YAML writes an empty mapping as ``None``; JSONB reads it back as ``{}``.

        The agent behaves identically either way, so reporting it as a divergence
        would bury the real differences in serialization noise.
        """
        assert config_divergence({"mcp_servers": None}, {"mcp_servers": {}}) == []
        assert config_divergence({"tools": None}, {"tools": []}) == []
        assert config_divergence({"a": {"b": None}}, {"a": {"b": {}}}) == []

    def test_a_populated_container_still_differs_from_an_empty_one(self):
        assert config_divergence({"mcp_servers": None}, {"mcp_servers": {"x": 1}}) == [
            "mcp_servers.x"
        ]

    def test_zero_and_false_are_not_treated_as_empty(self):
        """``0`` and ``False`` are real settings, not absent ones."""
        assert config_divergence({"keep": 0}, {"keep": None}) == ["keep"]
        assert config_divergence({"enabled": False}, {"enabled": None}) == ["enabled"]

    def test_a_dict_replaced_by_a_scalar_is_reported_at_that_path(self):
        assert config_divergence({"a": {"b": 1}}, {"a": 5}) == ["a"]

    def test_lists_compare_by_value_not_by_recursion(self):
        assert config_divergence(
            {"modes": ["RAGAS"]}, {"modes": ["RAGAS", "SOURCES"]}
        ) == ["modes"]
        assert config_divergence({"modes": ["RAGAS"]}, {"modes": ["RAGAS"]}) == []

    def test_non_dict_inputs_are_reported_rather_than_raising(self):
        """A caller that hands us a missing config gets a finding, not a crash."""
        assert config_divergence(None, {"a": 1}) == ["a"]
        assert config_divergence({"a": 1}, None) == ["a"]


class TestCorpusFingerprint:
    """A digest that is equal exactly when the corpus content is equal."""

    def test_is_independent_of_row_order(self):
        rows = [("aaa", 10), ("bbb", 20), ("ccc", 30)]
        assert corpus_fingerprint(rows) == corpus_fingerprint(list(reversed(rows)))

    def test_changes_when_a_document_size_changes(self):
        assert corpus_fingerprint([("aaa", 10)]) != corpus_fingerprint([("aaa", 11)])

    def test_changes_when_a_document_is_added(self):
        assert corpus_fingerprint([("aaa", 10)]) != corpus_fingerprint(
            [("aaa", 10), ("bbb", 20)]
        )

    def test_changes_when_a_document_is_removed(self):
        assert corpus_fingerprint([("aaa", 10), ("bbb", 20)]) != corpus_fingerprint(
            [("aaa", 10)]
        )

    def test_is_stable_across_calls(self):
        rows = [("aaa", 10), ("bbb", 20)]
        assert corpus_fingerprint(rows) == corpus_fingerprint(rows)

    def test_an_empty_corpus_has_its_own_digest(self):
        empty = corpus_fingerprint([])
        assert isinstance(empty, str) and empty
        assert empty != corpus_fingerprint([("aaa", 10)])

    def test_a_null_size_is_distinct_from_zero(self):
        """A document with no recorded size is not a zero-byte document."""
        assert corpus_fingerprint([("aaa", None)]) != corpus_fingerprint([("aaa", 0)])

    def test_field_separator_cannot_be_forged_from_a_hash_value(self):
        """Two corpora must not collide because a value contains the separator."""
        assert corpus_fingerprint([("a", 1), ("b", 2)]) != corpus_fingerprint(
            [("a:1:b", 2)]
        )

    def test_is_prefixed_so_the_digest_algorithm_is_readable(self):
        assert corpus_fingerprint([("aaa", 10)]).startswith("sha256:")

    def test_rejects_a_row_that_is_not_a_pair(self):
        with pytest.raises(ValueError):
            corpus_fingerprint([("aaa",)])
