"""The report must show which code and which config produced the run.

The HTML reports in ``bench_out/`` recorded none of this: no commit, no corpus
id, not even ``context_window``. ``parse_benchmark_results`` took ``metadata`` and
kept only ``time``. A reader comparing ``bench-8192-...report.html`` against a
32768 run had nothing in either page to tell them apart -- and the underlying
JSON, had they opened it, would have told them both runs were 32768.

These version lines sit inside the existing Run provenance block. Divergence and
corpus stability there say whether a report describes its own run; the digests
here say whether two runs are comparable at all.
"""

from src.utils.generate_benchmark_report import (
    format_provenance_html,
    format_version_html,
    parse_benchmark_results,
)

CODE_VERSION = {
    "digest": "sha256:abcdef1234567890",
    "source": "content digest of the `src` package files in the benchmark image",
    "file_count": 214,
    "deploy_git_commit": "0a157cdce02de6a3e3455c82a91bfbddfa00a0d9",
    "deploy_git_dirty": False,
    "deploy_git_note": "Written once by `archi create` and then frozen: ...",
}

CONFIG_VERSION = {
    "digest": "sha256:fedcba0987654321",
    "source": "effective configuration: what the agent read from Postgres, overlaid ...",
    "selected_file": "/root/archi/config.yaml",
    "selected_file_digest": "sha256:1111222233334444",
    "divergence_from_selected_file": [],
    "key_settings": {
        "services.chat_app.context_editing": {"context_window": 8192, "keep": 1},
        "services.benchmarking.agent_md_file": "/root/archi/agents/v1.md",
    },
}

PROVENANCE = {
    "configuration_divergence": [],
    "corpus_fingerprint_before": "sha256:c0rpu5",
    "corpus_fingerprint": "sha256:c0rpu5",
    "corpus_unchanged_at_endpoints": True,
    "code_version": CODE_VERSION,
    "config_version": CONFIG_VERSION,
}


class TestVersionLines:
    def test_shows_the_code_digest(self):
        assert "sha256:abcdef1234567890" in format_version_html(PROVENANCE)

    def test_shows_the_config_digest(self):
        assert "sha256:fedcba0987654321" in format_version_html(PROVENANCE)

    def test_shows_the_arm_defining_setting(self):
        """The whole point: 8192 must be visible on the page."""
        html = format_version_html(PROVENANCE)

        assert "context_editing" in html
        assert "8192" in html

    def test_shows_the_sweep_arm_setting(self):
        assert "/root/archi/agents/v1.md" in format_version_html(PROVENANCE)

    def test_labels_the_deploy_commit_as_deploy_scoped(self):
        """So a reader does not mistake the frozen value for the arm's code."""
        html = format_version_html(PROVENANCE)

        assert "0a157cdce0" in html
        assert "not the image this run used" in html

    def test_a_dirty_deploy_tree_is_marked(self):
        provenance = dict(
            PROVENANCE, code_version=dict(CODE_VERSION, deploy_git_dirty=True)
        )

        assert "dirty tree" in format_version_html(provenance)

    def test_a_missing_code_digest_says_it_was_not_recorded(self):
        """Regenerating an Aug-11 report must not invent a version it never had."""
        provenance = dict(PROVENANCE, code_version={"digest": None})

        assert "not recorded" in format_version_html(provenance).lower()

    def test_no_settings_table_when_the_arm_has_no_key_settings(self):
        """An older artifact may carry a digest but no compact view."""
        provenance = dict(
            PROVENANCE, config_version=dict(CONFIG_VERSION, key_settings={})
        )
        html = format_version_html(provenance)

        assert "Settings that define this arm" not in html
        assert "sha256:fedcba0987654321" in html

    def test_a_scalar_setting_renders_without_json_quoting(self):
        provenance = dict(
            PROVENANCE,
            config_version=dict(
                CONFIG_VERSION,
                key_settings={"services.chat_app.recursion_limit": 50},
            ),
        )
        html = format_version_html(provenance)

        assert "<code>50</code>" in html

    def test_a_list_setting_renders_as_json(self):
        provenance = dict(
            PROVENANCE,
            config_version=dict(
                CONFIG_VERSION,
                key_settings={"services.benchmarking.modes": ["RAGAS", "SOURCES"]},
            ),
        )

        assert "RAGAS" in format_version_html(provenance)

    def test_an_artifact_with_no_version_blocks_renders_nothing(self):
        provenance = {"configuration_divergence": [], "corpus_fingerprint": "x"}

        assert format_version_html(provenance) == ""

    def test_empty_provenance_does_not_crash(self):
        assert format_version_html({}) == ""
        assert format_version_html(None) == ""

    def test_values_are_html_escaped(self):
        provenance = dict(
            PROVENANCE,
            config_version=dict(CONFIG_VERSION, source="<script>alert(1)</script>"),
        )

        assert "<script>" not in format_version_html(provenance)

    def test_key_settings_are_html_escaped(self):
        provenance = dict(
            PROVENANCE,
            config_version=dict(
                CONFIG_VERSION, key_settings={"<script>": "<img onerror=x>"}
            ),
        )
        html = format_version_html(provenance)

        assert "<script>" not in html
        assert "<img" not in html


class TestPanelIntegration:
    def test_the_version_lines_appear_inside_the_provenance_block(self):
        html = format_provenance_html(PROVENANCE)

        assert "Run provenance" in html
        assert "sha256:fedcba0987654321" in html

    def test_the_existing_divergence_reporting_is_unaffected(self):
        diverged = dict(
            PROVENANCE,
            configuration_divergence=[
                "services.chat_app.context_editing.context_window"
            ],
        )
        html = format_provenance_html(diverged)

        assert "did <strong>not</strong> use the" in html
        assert "services.chat_app.context_editing.context_window" in html

    def test_a_report_predating_versions_still_renders_the_block(self):
        legacy = {
            "configuration_divergence": [],
            "corpus_fingerprint": None,
            "corpus_unchanged_at_endpoints": None,
        }

        assert "Run provenance" in format_provenance_html(legacy)


class TestParseSuppliesVersions:
    def test_the_config_version_comes_off_the_rendered_record(self):
        """A sweep page must be captioned with its own arm, not another's."""
        results = [
            {"config_version": CONFIG_VERSION, "configuration": {}},
            {"config_version": {"digest": "sha256:other"}, "configuration": {}},
        ]

        *_, provenance = parse_benchmark_results(results, {})

        assert provenance["config_version"] == CONFIG_VERSION

    def test_the_code_version_comes_off_the_metadata(self):
        """One image runs every arm, so the code version is per invocation."""
        *_, provenance = parse_benchmark_results(
            [{"configuration": {}}], {"code_version": CODE_VERSION}
        )

        assert provenance["code_version"] == CODE_VERSION

    def test_an_artifact_without_versions_reports_none(self):
        *_, provenance = parse_benchmark_results([{"configuration": {}}], {})

        assert provenance["config_version"] is None
        assert provenance["code_version"] is None
