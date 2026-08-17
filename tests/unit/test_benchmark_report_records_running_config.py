"""``handle_results`` must record the configuration the agent actually used.

Two sources were wrong, in sequence:

1. It re-read the YAML file from disk at report time, but the agent reads
   Postgres. When those diverged the report labelled the run with settings it
   never used -- an 8192-token run was written up as 32768.
2. Reading Postgres at report time is still wrong. ``archi.__init__`` snapshots
   ``get_full_config()`` when the chain is constructed, which happens before the
   arm's questions run. A config change during the arm would make a fresh query
   certify settings the chain never held -- and would *clear* the divergence
   list at the same time, turning a detectable fault into a silent one. In the
   incident that prompted this work the re-seed landed 59 seconds after the
   report was written; a slightly different ordering would have produced a
   confidently wrong report.

So the run's configuration is handed in from the chain that produced the
answers. The file is still recorded -- it is what the operator selected, and
what a sweep varies -- but it is no longer the only account of the run.
"""

import pytest
import yaml

import src.bin.service_benchmark as sb
from src.bin.service_benchmark import ResultHandler


@pytest.fixture(autouse=True)
def _reset_results(monkeypatch):
    monkeypatch.setattr(ResultHandler, "results", [])


def _write(tmp_path, config):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


FILE_CONFIG = {
    "services": {"chat_app": {"context_editing": {"context_window": 32768, "keep": 1}}}
}
CHAIN_CONFIG = {
    "services": {"chat_app": {"context_editing": {"context_window": 8192, "keep": 1}}}
}


def test_records_the_configuration_the_chain_held(tmp_path):
    ResultHandler.handle_results(
        _write(tmp_path, FILE_CONFIG), {}, {}, running_config=CHAIN_CONFIG
    )

    record = ResultHandler.results[0]
    assert record["configuration"] == FILE_CONFIG
    assert record["running_configuration"] == CHAIN_CONFIG


def test_records_the_very_object_the_chain_held(tmp_path):
    """Identity, not equality: nothing re-derives or re-reads the config."""
    ResultHandler.handle_results(
        _write(tmp_path, FILE_CONFIG), {}, {}, running_config=CHAIN_CONFIG
    )

    assert ResultHandler.results[0]["running_configuration"] is CHAIN_CONFIG


def test_does_not_read_the_config_at_report_time(tmp_path, monkeypatch):
    """A config change during the arm must not rewrite the run's own history.

    Reading the config here would report it as it stands after the questions
    ran, not as the chain held it -- and would silently clear the divergence
    list at the same time. Any read through the module's config accessors fails
    this test, not just the one that used to be here.
    """

    def _forbidden(*args, **kwargs):
        raise AssertionError("handle_results must not read config at report time")

    monkeypatch.setattr(sb, "get_static_config", _forbidden)
    assert not hasattr(sb, "get_full_config")

    ResultHandler.handle_results(
        _write(tmp_path, FILE_CONFIG), {}, {}, running_config=CHAIN_CONFIG
    )

    assert ResultHandler.results[0]["running_configuration"] is CHAIN_CONFIG


def test_names_the_setting_the_report_would_have_misattributed(tmp_path):
    ResultHandler.handle_results(
        _write(tmp_path, FILE_CONFIG), {}, {}, running_config=CHAIN_CONFIG
    )

    assert ResultHandler.results[0]["configuration_divergence"] == [
        "services.chat_app.context_editing.context_window"
    ]


def test_divergence_is_empty_when_the_file_describes_the_run(tmp_path):
    ResultHandler.handle_results(
        _write(tmp_path, FILE_CONFIG), {}, {}, running_config=FILE_CONFIG
    )

    assert ResultHandler.results[0]["configuration_divergence"] == []


def test_warns_when_the_run_did_not_use_the_selected_configuration(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        ResultHandler.handle_results(
            _write(tmp_path, FILE_CONFIG), {}, {}, running_config=CHAIN_CONFIG
        )

    assert "context_window" in caplog.text


def test_no_warning_when_the_configurations_agree(tmp_path, caplog):
    with caplog.at_level("WARNING"):
        ResultHandler.handle_results(
            _write(tmp_path, FILE_CONFIG), {}, {}, running_config=FILE_CONFIG
        )

    assert caplog.text == ""


def test_an_unavailable_running_config_does_not_discard_the_results(tmp_path):
    """A finished benchmark must not lose its scores because provenance failed."""
    ResultHandler.handle_results(
        _write(tmp_path, FILE_CONFIG), {"q": 1}, {"total": 2}, running_config=None
    )

    record = ResultHandler.results[0]
    assert record["single_question_results"] == {"q": 1}
    assert record["running_configuration"] is None
    assert record["configuration_divergence"] == [
        "<unavailable: the run reported no configuration>"
    ]


def _pin_corpus(monkeypatch, value):
    monkeypatch.setattr(
        ResultHandler, "get_corpus_fingerprint", staticmethod(lambda: value)
    )


def test_records_the_corpus_each_arm_was_scored_against(tmp_path, monkeypatch):
    """Per arm, not once per sweep -- a corpus change between arms must show."""
    _pin_corpus(monkeypatch, "sha256:deadbeef")

    ResultHandler.handle_results(
        _write(tmp_path, FILE_CONFIG),
        {},
        {},
        running_config=FILE_CONFIG,
        corpus_before="sha256:deadbeef",
    )

    record = ResultHandler.results[0]
    assert record["corpus_fingerprint"] == "sha256:deadbeef"
    assert record["corpus_fingerprint_before"] == "sha256:deadbeef"
    assert record["corpus_stable"] is True


def test_detects_a_corpus_that_changed_while_the_arm_was_running(
    tmp_path, monkeypatch, caplog
):
    """Sampling only after the questions would report the final state as if it
    had covered the whole arm. Ingestion runs continuously in this deployment,
    so an arm can straddle a re-ingest and score different questions against
    different corpora.
    """
    _pin_corpus(monkeypatch, "sha256:after")

    with caplog.at_level("WARNING"):
        ResultHandler.handle_results(
            _write(tmp_path, FILE_CONFIG),
            {},
            {},
            running_config=FILE_CONFIG,
            corpus_before="sha256:before",
        )

    record = ResultHandler.results[0]
    assert record["corpus_stable"] is False
    assert record["corpus_fingerprint_before"] == "sha256:before"
    assert record["corpus_fingerprint"] == "sha256:after"
    assert "corpus" in caplog.text.lower()


def test_an_unsampled_corpus_is_unknown_rather_than_stable(tmp_path, monkeypatch):
    """Absent a before-reading, stability is undetermined -- never assumed true."""
    _pin_corpus(monkeypatch, "sha256:after")

    ResultHandler.handle_results(
        _write(tmp_path, FILE_CONFIG),
        {},
        {},
        running_config=FILE_CONFIG,
        corpus_before=None,
    )

    assert ResultHandler.results[0]["corpus_stable"] is None


def test_prompts_are_mapped_in_the_file_config_as_before(tmp_path):
    """The existing prompt-inlining behaviour is unchanged by the new fields."""
    prompt = tmp_path / "p.txt"
    prompt.write_text("PROMPT BODY")
    config = {
        "services": {"benchmarking": {"prompts": {"main": {"greet": str(prompt)}}}}
    }

    ResultHandler.handle_results(
        _write(tmp_path, config), {}, {}, running_config=config
    )

    recorded = ResultHandler.results[0]["configuration"]
    assert (
        recorded["services"]["benchmarking"]["prompts"]["main"]["greet"]
        == "PROMPT BODY"
    )


def test_a_missing_prompt_file_is_left_as_its_path(tmp_path):
    """A prompt that cannot be read stays a path rather than failing the report."""
    missing = str(tmp_path / "absent.txt")
    config = {"services": {"benchmarking": {"prompts": {"main": {"greet": missing}}}}}

    ResultHandler.handle_results(
        _write(tmp_path, config), {}, {}, running_config=config
    )

    recorded = ResultHandler.results[0]["configuration"]
    assert recorded["services"]["benchmarking"]["prompts"]["main"]["greet"] == missing
