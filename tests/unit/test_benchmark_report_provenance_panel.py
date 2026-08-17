"""The HTML report must show which code and which config produced the run.

The HTML reports in ``bench_out/`` are what a human actually reads, and they
recorded none of this: no commit, no corpus id, not even ``context_window``.
``parse_benchmark_results`` took ``metadata`` and kept only ``time``.

A reader comparing ``bench-8192-...report.html`` against a 32768 run had nothing
in either page to tell them apart -- and the underlying JSON, had they opened it,
would have told them both runs were 32768.
"""

from src.utils.generate_benchmark_report import format_html_output, provenance_html

CODE_VERSION = {
    "digest": "sha256:abcdef1234567890",
    "source": "content digest of the `src` modules loaded in the benchmark image",
    "module_count": 214,
    "deploy_git_commit": "0a157cdce02de6a3e3455c82a91bfbddfa00a0d9",
    "deploy_git_dirty": False,
    "deploy_git_note": "Written once by `archi create` and then frozen: ...",
}

CONFIG_VERSION = {
    "digest": "sha256:fedcba0987654321",
    "source": "running configuration read from Postgres (what the agent used)",
    "selected_file": "/root/archi/config.yaml",
    "selected_file_digest": "sha256:1111222233334444",
    "divergence_from_selected_file": [],
    "key_settings": {
        "services.chat_app.context_editing": {"context_window": 8192, "keep": 1}
    },
}

METADATA = {
    "time": "2026-08-17 17:08:50+00:00",
    "code_version": CODE_VERSION,
    "config_versions": [CONFIG_VERSION["digest"]],
    "corpus_fingerprint": "sha256:c0rpu5",
    "corpus_snapshot_id": "16658901-dead-beef",
}


class TestProvenancePanel:
    def test_shows_the_code_digest(self):
        assert "sha256:abcdef1234567890" in provenance_html(
            METADATA, config_version=CONFIG_VERSION
        )

    def test_shows_the_config_digest(self):
        assert "sha256:fedcba0987654321" in provenance_html(
            METADATA, config_version=CONFIG_VERSION
        )

    def test_shows_the_arm_defining_setting(self):
        """The whole point: 8192 must be visible on the page."""
        html = provenance_html(METADATA, config_version=CONFIG_VERSION)

        assert "context_editing" in html
        assert "8192" in html

    def test_labels_the_deploy_commit_as_deploy_scoped(self):
        """So a reader does not mistake the frozen value for the arm's code."""
        html = provenance_html(METADATA, config_version=CONFIG_VERSION)

        assert "0a157cdce0" in html
        assert "deploy" in html.lower()

    def test_shows_the_corpus_fingerprint(self):
        assert "sha256:c0rpu5" in provenance_html(
            METADATA, config_version=CONFIG_VERSION
        )

    def test_a_divergence_is_shown_as_a_warning(self):
        diverged = dict(
            CONFIG_VERSION,
            divergence_from_selected_file=[
                "services.chat_app.context_editing.context_window"
            ],
        )

        html = provenance_html(METADATA, config_version=diverged)

        assert "services.chat_app.context_editing.context_window" in html
        assert "may not describe" in html.lower() or "warning" in html.lower()

    def test_no_divergence_produces_no_warning(self):
        assert (
            "may not describe"
            not in provenance_html(METADATA, config_version=CONFIG_VERSION).lower()
        )

    def test_legacy_metadata_says_what_was_not_recorded(self):
        """Regenerating an Aug-11 report must not invent a version it never had."""
        legacy = {
            "time": "2026-08-11 02:54:32+00:00",
            "git_info": {"last_commit": "0a157cdce0\n", "git_diff": ""},
            "corpus_snapshot_id": "d6541cbf",
        }

        html = provenance_html(legacy)

        assert "not recorded" in html.lower()
        assert "0a157cdce0" in html

    def test_empty_metadata_does_not_crash(self):
        assert isinstance(provenance_html({}), str)

    def test_values_are_html_escaped(self):
        hostile = dict(CONFIG_VERSION, selected_file="<script>x")

        assert "<script>" not in provenance_html({}, config_version=hostile)

    def test_a_sweeps_arm_is_captioned_with_its_own_settings(self):
        """One invocation runs every arm; arm 1's page must not show arm 3's."""
        arm_one = dict(
            CONFIG_VERSION,
            key_settings={
                "services.chat_app.context_editing": {"context_window": 4096}
            },
        )

        html = provenance_html(METADATA, config_version=arm_one)

        assert "4096" in html
        assert "8192" not in html


class TestReportEmbedsProvenance:
    _CONFIG = {"services": {"benchmarking": {"modes": []}}}

    def test_the_report_carries_the_config_digest(self):
        html = format_html_output(
            self._CONFIG,
            "ragas-bench",
            "2026-08-17",
            {},
            {},
            metadata=METADATA,
            config_version=CONFIG_VERSION,
        )

        assert "sha256:fedcba0987654321" in html

    def test_the_report_carries_the_arm_setting(self):
        html = format_html_output(
            self._CONFIG,
            "ragas-bench",
            "2026-08-17",
            {},
            {},
            metadata=METADATA,
            config_version=CONFIG_VERSION,
        )

        assert "8192" in html

    def test_metadata_is_optional_so_existing_callers_still_work(self):
        html = format_html_output(self._CONFIG, "ragas-bench", "2026-08-17", {}, {})

        assert "Benchmark Results Comparison" in html
