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

from src.utils.benchmark_provenance import (
    asserted_config_divergence,
    config_divergence,
    corpus_fingerprint,
)


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

    def test_an_empty_mapping_is_not_an_empty_sequence(self):
        """``None`` means "not configured" and matches either empty container,
        but ``{}`` and ``[]`` are different settings and must not be collapsed.
        """
        assert config_divergence({"tools": {}}, {"tools": []}) == ["tools"]

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

    def test_accepts_opaque_string_values(self):
        """Values are not numbers.

        ``resource_hash`` is ``md5(url)`` -- an identity hash deliberately stable
        across content edits -- so document size alone cannot detect a changed
        document. The caller also feeds in per-chunk digests, which are hex
        strings, so the value side must stay opaque.
        """
        digest = corpus_fingerprint([("chunk:1:0", "d41d8cd98f00b204e9800998ecf8427e")])
        assert digest.startswith("sha256:")

    def test_a_rechunked_corpus_differs_from_the_original(self):
        before = corpus_fingerprint([("chunk:1:0", "aaa"), ("chunk:1:1", "bbb")])
        after = corpus_fingerprint(
            [("chunk:1:0", "aaa"), ("chunk:1:1", "bbb"), ("chunk:1:2", "ccc")]
        )
        assert before != after

    def test_an_edit_that_preserves_document_size_still_changes_the_digest(self):
        """The failure mode that document size alone cannot see."""
        same_size_doc = ("doc:abc", "1024")
        before = corpus_fingerprint([same_size_doc, ("chunk:1:0", "old-content-hash")])
        after = corpus_fingerprint([same_size_doc, ("chunk:1:0", "new-content-hash")])
        assert before != after


class TestAssertedConfigDivergence:
    """Only what the selected file ASSERTS can be a mislabel.

    ``config_divergence`` compares two configurations symmetrically, which is the
    right primitive but the wrong question here. The selected file is sparse
    operator intent; ``get_full_config()`` returns the configuration *after*
    seeding, defaulting and reshaping. They differ by design at roughly two
    hundred paths, so comparing them whole reported every run as mislabelled and
    ``arms_comparable()`` was False on every arm of every run -- stripping every
    leaderboard rank and A/B winner, unconditionally.

    Measured on the file that actually seeded archi-dev against what archi-dev
    then served -- the same source by construction -- the whole-dict comparison
    reported 192 paths and this one reports 1, while still catching the
    8192-vs-32768 mislabel that motivated the check.
    """

    def test_a_key_only_the_running_config_has_is_not_a_divergence(self):
        """get_full_config synthesizes config_version and available_* itself.

        No YAML file has them, so reporting them means reporting them forever.
        """
        selected = {"services": {"chat_app": {"recursion_limit": 50}}}
        running = {
            "config_version": "2.0.0",
            "available_models": ["a", "b"],
            "available_pipelines": ["p"],
            "available_providers": ["q"],
            "services": {"chat_app": {"recursion_limit": 50}},
        }

        assert asserted_config_divergence(selected, running) == []

    def test_a_section_the_file_omits_entirely_is_not_a_divergence(self):
        """The seeder fills defaults for sections the operator never wrote.

        archi-dev's seed file has no `global` and no `mcp_servers`; the running
        config has both.
        """
        selected = {"services": {"chat_app": {"recursion_limit": 50}}}
        running = {
            "global": {"log_level": "INFO"},
            "mcp_servers": {"x": {"url": "http://y"}},
            "services": {"chat_app": {"recursion_limit": 50}},
        }

        assert asserted_config_divergence(selected, running) == []

    def test_a_reshaped_duplicate_of_a_section_is_not_a_divergence(self):
        """The file writes data_manager.sources; the running config also exposes
        a top-level `sources` copy. That reshaping was 48 spurious paths."""
        selected = {"data_manager": {"sources": {"docs": {"enabled": True}}}}
        running = {
            "data_manager": {"sources": {"docs": {"enabled": True}}},
            "sources": {"docs": {"enabled": True, "schedule": "daily"}},
        }

        assert asserted_config_divergence(selected, running) == []

    def test_a_setting_the_file_asserts_and_the_agent_contradicts_is_reported(self):
        selected = {"services": {"chat_app": {"recursion_limit": 50}}}
        running = {"services": {"chat_app": {"recursion_limit": 10}}}

        assert asserted_config_divergence(selected, running) == [
            "services.chat_app.recursion_limit"
        ]

    def test_a_setting_the_file_asserts_and_the_agent_lacks_is_reported(self):
        """Asking for something the agent never received is still a mislabel."""
        selected = {"services": {"chat_app": {"recursion_limit": 50}}}
        running = {"services": {"chat_app": {}}}

        assert asserted_config_divergence(selected, running) == [
            "services.chat_app.recursion_limit"
        ]

    def test_a_whole_section_missing_from_the_running_config_is_reported(self):
        selected = {"services": {"chat_app": {"recursion_limit": 50}}}
        running = {"services": {}}

        assert asserted_config_divergence(selected, running) == [
            "services.chat_app.recursion_limit"
        ]

    def test_the_incident_this_check_exists_for_is_still_reported(self):
        """The whole point: narrowing the comparison must not blind it."""
        selected = {
            "services": {
                "chat_app": {"context_editing": {"context_window": 32768, "keep": 1}}
            }
        }
        running = {
            "config_version": "2.0.0",
            "available_models": ["a"],
            "global": {"log_level": "INFO"},
            "services": {
                "chat_app": {"context_editing": {"context_window": 8192, "keep": 1}}
            },
        }

        assert asserted_config_divergence(selected, running) == [
            "services.chat_app.context_editing.context_window"
        ]

    def test_services_benchmarking_is_ignored_because_it_bypasses_postgres(self):
        """The harness reads these from the file and passes them to archi().

        They never reach Postgres, so the seeded values persist across every arm
        of a sweep -- 11 spurious paths per arm on the real fasrc-cannon sweep.
        """
        selected = {
            "services": {
                "benchmarking": {"agent_md_file": "/agents/v2.md", "model": "qwen"}
            }
        }
        running = {
            "services": {
                "benchmarking": {"agent_md_file": "/agents/v1.md", "model": "other"}
            }
        }

        assert asserted_config_divergence(selected, running) == []

    def test_name_is_ignored_because_the_two_sides_mean_different_things(self):
        """Running `name` is the deployment name; the file's is the config's."""
        selected = {"name": "fasrc-cannon-v1-strict"}
        running = {"name": "archi_dev"}

        assert asserted_config_divergence(selected, running) == []

    def test_paths_the_deploy_rewrites_into_the_container_are_ignored(self):
        """`archi create` replaces host paths with fixed container paths.

        Without this, `services.chat_app.agents_dir` diverged on every deployment
        forever -- on archi-dev the file says
        /home/austin/Projects/archi/deploy/fasrc-dev/agents and the running
        configuration says /root/archi/agents -- which left the guard failing even
        after the comparison was scoped.
        """
        selected = {
            "services": {
                "chat_app": {
                    "agents_dir": "/home/austin/deploy/fasrc-dev/agents",
                    "skills_dir": "/home/austin/deploy/fasrc-dev/skills",
                }
            }
        }
        running = {
            "services": {
                "chat_app": {
                    "agents_dir": "/root/archi/agents",
                    "skills_dir": "/root/archi/skills",
                }
            }
        }

        assert asserted_config_divergence(selected, running) == []

    def test_the_rewrite_exemption_covers_every_service_that_gets_one(self):
        """templates_manager rewrites chat_app, redmine_mailbox and piazza."""
        for service in ("chat_app", "redmine_mailbox", "piazza"):
            selected = {"services": {service: {"agents_dir": "/host/agents"}}}
            running = {"services": {service: {"agents_dir": "/root/archi/agents"}}}

            assert asserted_config_divergence(selected, running) == [], service

    def test_a_rewritten_path_exemption_does_not_excuse_its_siblings(self):
        selected = {
            "services": {
                "chat_app": {"agents_dir": "/host/agents", "recursion_limit": 50}
            }
        }
        running = {
            "services": {
                "chat_app": {"agents_dir": "/root/archi/agents", "recursion_limit": 10}
            }
        }

        assert asserted_config_divergence(selected, running) == [
            "services.chat_app.recursion_limit"
        ]

    def test_an_ignored_subtree_does_not_hide_its_siblings(self):
        selected = {
            "services": {
                "benchmarking": {"model": "a"},
                "chat_app": {"recursion_limit": 50},
            }
        }
        running = {
            "services": {
                "benchmarking": {"model": "b"},
                "chat_app": {"recursion_limit": 10},
            }
        }

        assert asserted_config_divergence(selected, running) == [
            "services.chat_app.recursion_limit"
        ]

    def test_the_ignored_paths_are_overridable(self):
        selected = {"name": "one"}
        running = {"name": "two"}

        assert asserted_config_divergence(selected, running, ignore_paths=()) == [
            "name"
        ]

    def test_identical_configs_have_no_divergence(self):
        cfg = {"services": {"chat_app": {"context_editing": {"context_window": 8192}}}}

        assert asserted_config_divergence(cfg, dict(cfg)) == []

    def test_a_file_that_asserts_nothing_reports_nothing(self):
        assert asserted_config_divergence({}, {"anything": 1}) == []
        assert asserted_config_divergence(None, {"anything": 1}) == []

    def test_every_differing_asserted_path_is_reported_sorted(self):
        selected = {"a": 1, "b": {"y": 2, "x": 3}}
        running = {"a": 9, "b": {"y": 8, "x": 3}}

        assert asserted_config_divergence(selected, running) == ["a", "b.y"]

    def test_the_none_and_empty_container_equivalence_is_preserved(self):
        """Same leaf semantics as config_divergence -- YAML writes {} as None."""
        assert (
            asserted_config_divergence({"mcp_servers": None}, {"mcp_servers": {}}) == []
        )
        assert asserted_config_divergence({"tools": None}, {"tools": []}) == []

    def test_zero_and_false_are_still_settings_not_absences(self):
        assert asserted_config_divergence({"keep": 0}, {"keep": None}) == ["keep"]
        assert asserted_config_divergence({"enabled": False}, {"enabled": None}) == [
            "enabled"
        ]
