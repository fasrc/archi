"""The HTML report must not present the selected config as the config that ran.

The JSON record now carries both the selected configuration and the one the
chain actually held, but the HTML is the artifact a person reads. While it
rendered only ``configuration``, the original defect survived in full: a run
executed at 8192 was still displayed as 32768, and the divergence warning
existed only in a container log nobody opens.

Note what is deliberately NOT swapped. ``config_data`` still comes from the
file, because the benchmark harness genuinely reads its own settings there --
``services.benchmarking.modes`` drives which sections render, and the harness
took those from the file. Only the agent reads Postgres. Presenting the running
configuration as the source of the harness's own settings would trade one wrong
label for another; the provenance is shown alongside instead.
"""

from src.utils.generate_benchmark_report import (
    format_html_output,
    parse_benchmark_results,
)

FILE_CONFIG = {
    "services": {
        "benchmarking": {"modes": ["RAGAS"]},
        "chat_app": {"context_editing": {"context_window": 32768}},
    }
}
CHAIN_CONFIG = {
    "services": {
        "benchmarking": {"modes": ["RAGAS"]},
        "chat_app": {"context_editing": {"context_window": 8192}},
    }
}


def _results(**overrides):
    record = {
        "single_question_results": {},
        "total_results": {},
        "configuration_file": "configs/config.yaml",
        "configuration": FILE_CONFIG,
        "running_configuration": CHAIN_CONFIG,
        "configuration_divergence": [
            "services.chat_app.context_editing.context_window"
        ],
        "corpus_fingerprint_before": "sha256:aaa",
        "corpus_fingerprint": "sha256:aaa",
        "corpus_unchanged_at_endpoints": True,
    }
    record.update(overrides)
    return [record], {"time": "2026-08-17"}


def _html(**overrides):
    results, metadata = _results(**overrides)
    config_data, name, ts, questions, totals, provenance = parse_benchmark_results(
        results, metadata
    )
    return format_html_output(config_data, name, ts, questions, totals, provenance)


def test_parse_returns_the_provenance_recorded_for_the_run():
    results, metadata = _results()

    _, _, _, _, _, provenance = parse_benchmark_results(results, metadata)

    assert provenance["running_configuration"] == CHAIN_CONFIG
    assert provenance["configuration_divergence"] == [
        "services.chat_app.context_editing.context_window"
    ]
    assert provenance["corpus_unchanged_at_endpoints"] is True


def test_harness_settings_still_come_from_the_selected_file():
    """`modes` drives which sections render and the harness read it from the file."""
    results, metadata = _results()

    config_data, _, _, _, _, _ = parse_benchmark_results(results, metadata)

    assert config_data == FILE_CONFIG


def test_html_names_the_setting_the_run_did_not_use():
    html = _html()

    assert "services.chat_app.context_editing.context_window" in html


def test_html_says_so_when_the_run_matched_the_selected_configuration():
    html = _html(configuration_divergence=[], running_configuration=FILE_CONFIG)

    assert "services.chat_app.context_editing.context_window" not in html
    assert "matches" in html.lower()


class TestAnUnrecordedComparisonIsNotAMatch:
    """Codex finding 2 on #272.

    An artifact written before provenance existed has no
    ``configuration_divergence`` key at all. Collapsing that to ``[]`` made the
    report state that the agent's configuration *matches* the selected file --
    a positive claim about a comparison that was never performed, and on exactly
    the historical runs whose mislabelling prompted this work.

    ``[]`` and absence must stay distinct: the first means compared-and-agreed,
    the second means nothing is known. The corpus block a few lines below already
    draws that distinction; this one did not.
    """

    def test_a_missing_divergence_key_survives_parsing_as_none(self):
        results, metadata = _results()
        del results[0]["configuration_divergence"]

        _, _, _, _, _, provenance = parse_benchmark_results(results, metadata)

        assert provenance["configuration_divergence"] is None

    def test_an_empty_list_still_parses_as_an_agreed_comparison(self):
        results, metadata = _results(configuration_divergence=[])

        _, _, _, _, _, provenance = parse_benchmark_results(results, metadata)

        assert provenance["configuration_divergence"] == []

    def test_the_html_reports_unknown_rather_than_claiming_a_match(self):
        results, metadata = _results()
        del results[0]["configuration_divergence"]
        del results[0]["running_configuration"]
        parsed = parse_benchmark_results(results, metadata)
        html = format_html_output(*parsed)

        assert "matches" not in html.lower()
        assert "not recorded" in html.lower()

    def test_the_block_still_renders_for_such_an_artifact(self):
        results, metadata = _results()
        del results[0]["configuration_divergence"]
        parsed = parse_benchmark_results(results, metadata)

        assert "Run provenance" in format_html_output(*parsed)


def test_html_flags_a_corpus_that_changed_during_the_run():
    html = _html(
        corpus_unchanged_at_endpoints=False,
        corpus_fingerprint_before="sha256:aaa",
        corpus_fingerprint="sha256:bbb",
    )

    assert "sha256:aaa" in html and "sha256:bbb" in html
    assert "changed" in html.lower()


def test_html_does_not_claim_stability_it_cannot_prove():
    """Two samples prove the endpoints matched, not that nothing changed between.

    Continuous ingestion could change the corpus and change it back while the
    questions ran, producing identical endpoint readings. Claiming the corpus
    was "unchanged for the whole run" from that evidence is the same overclaim
    this whole change exists to remove.
    """
    html = _html()

    assert "whole run" not in html.lower()
    assert "start and the end" in html.lower()
    assert "reverted" in html.lower()


def test_html_distinguishes_unknown_corpus_stability_from_stable():
    html = _html(corpus_unchanged_at_endpoints=None)

    assert "unknown" in html.lower()


def test_html_still_renders_without_provenance():
    """Older result files carry no provenance; they must still produce a report."""
    results, metadata = _results()
    config_data, name, ts, questions, totals, _ = parse_benchmark_results(
        results, metadata
    )

    html = format_html_output(config_data, name, ts, questions, totals)

    assert "<html>" in html


def test_provenance_shows_time_to_ingest():
    """The HTML mirrors the markdown: seconds, plus a human-readable span."""
    html = _html(ingest_wall_seconds=7351.2)

    assert "Time to ingest" in html
    assert "7351 s" in html
    assert "2h 2m 31s" in html
    assert "every arm" in html


def test_provenance_says_not_measured_for_a_reused_corpus():
    html = _html(ingest_wall_seconds=None)

    assert "reused an existing corpus" in html
    assert "not measured" in html
    assert "0 s" not in html


def test_provenance_says_not_recorded_for_an_older_artifact():
    """`_results()` writes no such key, exactly as a pre-#417 artifact does.

    Asserted on wording unique to this branch: the version-stamp block has its
    own "not recorded" string, and would satisfy a looser check on its own.
    """
    html = _html()

    assert "Time to ingest" in html
    assert "predates the field" in html
    assert "reused an existing corpus" not in html


def test_parse_carries_ingest_wall_seconds_through_to_provenance():
    results, metadata = _results(ingest_wall_seconds=7351.2)

    _, _, _, _, _, provenance = parse_benchmark_results(results, metadata)

    assert provenance["ingest_wall_seconds"] == 7351.2
